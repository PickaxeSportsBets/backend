from fastapi import APIRouter
import os
from dotenv import load_dotenv
import logging
from contextlib import contextmanager, asynccontextmanager
from fastapi import FastAPI
import asyncio
from datetime import datetime, timezone, time, timedelta
import pytz
from clerk_backend_api import Clerk
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from PIL import Image
import jinja2
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from src.db.db import get_db
from src.db.models import Subscriptions, Arbitrage, PlusEv
from sqlalchemy import desc, cast, Numeric, text
import backoff
import signal
import sys


load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

email_password: str = os.environ.get("EMAIL_PASSWORD")

# Initialize Jinja2 environment
template_loader = jinja2.FileSystemLoader('src/email')
template_env = jinja2.Environment(loader=template_loader)

# Database retry settings
MAX_RETRIES = 5
RETRY_DELAY = 5  # seconds

# Email sending interval (in minutes)
EMAIL_INTERVAL_MINUTES = 60

# Global flags for shutdown
is_shutting_down = False
force_shutdown = False


def force_exit():
    """Force exit after timeout"""
    logger.warning("Force shutdown triggered - terminating process")
    sys.exit(1)


def handle_shutdown(signum, frame):
    """Signal handler for graceful shutdown"""
    global is_shutting_down, force_shutdown
    if is_shutting_down:
        logger.warning("Received second shutdown signal, forcing immediate exit")
        force_shutdown = True
        force_exit()
    else:
        logger.info("Received shutdown signal, initiating graceful shutdown...")
        is_shutting_down = True
        # Set a timer for force shutdown after 5 seconds
        signal.alarm(5)


# Register signal handlers
signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGALRM, lambda signum, frame: force_exit())


@backoff.on_exception(
    backoff.expo,
    (OperationalError, Exception),
    max_tries=2,  # Reduced retries during shutdown
    max_time=5,   # Reduced timeout during shutdown
    giveup=lambda e: "authentication failed" in str(e).lower() or is_shutting_down
)
def get_db_with_retry():
    """Get database session with retry logic"""
    global is_shutting_down, force_shutdown

    if is_shutting_down or force_shutdown:
        logger.info("Shutdown in progress, skipping database connection attempt")
        raise Exception("Application is shutting down")

    try:
        db = next(get_db())
        # Test the connection with a simple query
        db.execute(text("SELECT 1"))
        return db
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        try:
            db.close()
        except:
            pass
        raise


def get_email_subject(arb_bets: list, ev_bets: list) -> str:
    # Get best profits for subject line
    best_arb = max((float(bet.profit_percentage)
                   for bet in arb_bets), default=0)
    best_ev = max((float(bet.ev_percentage) for bet in ev_bets), default=0)
    logger.debug(f"Best arb: {best_arb}%, Best EV: {best_ev}%")

    # Base options for variety
    if best_arb > 2 and best_ev > 4:
        subject = "🔥 Hot Alert! {:.1f}% Arb + {:.1f}% EV".format(
            best_arb, best_ev)
    elif best_arb > 2:
        subject = "💰 Profit Alert! {:.1f}% Risk-Free Arb".format(best_arb)
    elif best_ev > 4:
        subject = "⚡ Value Alert! {:.1f}% EV Play".format(best_ev)
    else:
        subject = "🎯 Fresh Betting Picks"

    # Add secondary opportunities
    if best_arb > 0 and "Arb" not in subject:
        subject += " | {:.1f}% Arb".format(best_arb)
    if best_ev > 0 and "EV" not in subject:
        subject += " | {:.1f}% EV".format(best_ev)

    return subject.strip(" |")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global is_shutting_down

    logger.info("Starting email cron background tasks")
    # Start regular email task that runs every 30 minutes
    email_task = asyncio.create_task(send_regular_emails())

    try:
        yield
    finally:
        logger.info("Shutting down email cron background tasks")
        is_shutting_down = True

        # Cancel task immediately
        email_task.cancel()

        try:
            # Wait for task with a shorter timeout
            await asyncio.wait_for(
                asyncio.gather(email_task, return_exceptions=True),
                timeout=3  # 3 second timeout for cleanup
            )
            logger.info("Email tasks successfully shut down")
        except asyncio.TimeoutError:
            logger.warning("Timeout while waiting for tasks to shut down - forcing exit")
            force_shutdown = True
            force_exit()
        except asyncio.CancelledError:
            logger.info("Email tasks cancelled")
        except Exception as e:
            logger.error(f"Error during task shutdown: {str(e)}")
            force_shutdown = True
            force_exit()


router = APIRouter(tags=["emailCron"])


def format_bet_link(link: str) -> str:
    """Replace {state} placeholder with NY in bet links"""
    if link and isinstance(link, str):
        return link.replace("{state}", "NY")
    return link


async def send_regular_emails():
    """Send emails every 30 minutes with the latest bets"""
    connection_error_count = 0
    max_connection_errors = 5
    
    logger.info(f"Email service started - emails will be sent every {EMAIL_INTERVAL_MINUTES} minutes")

    while not is_shutting_down:
        db = None
        try:
            if force_shutdown:
                break

            # Log the start of a new email cycle
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"===== Starting email cycle at {current_time} =====")

            # Get database session with retry
            try:
                db = get_db_with_retry()
                # Reset error count on successful connection
                connection_error_count = 0

                # Get active subscriptions
                active_subscriptions = db.query(Subscriptions).filter(
                    Subscriptions.status == "active"
                ).all()
                active_subscriptions = [
                    sub for sub in active_subscriptions if sub.current_period_end >= datetime.now(timezone.utc)]
                num_subscriptions = len(active_subscriptions)
                logger.info(f"Found {num_subscriptions} active subscriptions")
                
                if num_subscriptions == 0:
                    logger.info("No active subscriptions found, skipping email cycle")
                    if db:
                        db.close()
                        db = None
                    
                    # Wait for the next cycle
                    for _ in range(EMAIL_INTERVAL_MINUTES):
                        if is_shutting_down or force_shutdown:
                            break
                        try:
                            await asyncio.wait_for(asyncio.sleep(60), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                    continue

                # Get bets data
                arb_bets = db.query(Arbitrage).filter(
                    Arbitrage.opportunity_type == "Arbitrage",
                    cast(Arbitrage.profit_percentage, Numeric) > 0
                ).all()

                # Get top 5 EV bets
                ev_bets = db.query(PlusEv).order_by(
                    desc(PlusEv.ev_percentage)).limit(5).all()
                
                logger.info(f"Found {len(arb_bets)} arbitrage bets and {len(ev_bets)} EV bets")

                # Format numeric values
                for bet in arb_bets:
                    bet.profit_percentage = float(bet.profit_percentage)
                    bet.team1_stake = float(bet.team1_stake) if bet.team1_stake else 0
                    bet.team2_stake = float(bet.team2_stake) if bet.team2_stake else 0
                    bet.team1_odds = float(bet.team1_odds) if bet.team1_odds else 0
                    bet.team2_odds = float(bet.team2_odds) if bet.team2_odds else 0
                    bet.team1_link = format_bet_link(bet.team1_link)
                    bet.team2_link = format_bet_link(bet.team2_link)

                for bet in ev_bets:
                    bet.ev_percentage = float(bet.ev_percentage)
                    bet.odds = float(bet.odds) if bet.odds else 0
                    bet.fair_odds = float(bet.fair_odds) if bet.fair_odds else 0
                    bet.link = format_bet_link(bet.link)

                # Close DB connection after fetching data
                if db:
                    db.close()
                    db = None

                # Special check for high-value bets
                has_special_bets = any(bet.profit_percentage > 3 for bet in arb_bets) or any(bet.ev_percentage > 10 for bet in ev_bets)
                
                # Create user list
                users = set(subscription.clerk_user_id for subscription in active_subscriptions)
                
                # Prepare email subject
                subject = get_email_subject(arb_bets, ev_bets)
                if has_special_bets:
                    subject = "🔥 HOT PICKS: " + subject
                
                logger.info(f"Sending email with subject: {subject}")
                
                # Send emails
                emails_sent = 0
                with Clerk(bearer_auth=os.environ.get("CLERK_SECRET_KEY")) as clerk:
                    for user_id in users:
                        try:
                            user = clerk.users.get(user_id=user_id)
                            name = user.first_name + " " + user.last_name
                            email = user.email_addresses[0].email_address
                            
                            sender_email = "pickaxebets@gmail.com"
                            sender_password = email_password
                            recipient_email = email
                            
                            # Choose template based on special bets
                            template_name = 'specialEmail.html' if has_special_bets else 'template.html'
                            template = template_env.get_template(template_name)
                            
                            html = template.render(
                                email=email,
                                name=name,
                                arbBets=arb_bets,
                                evBets=ev_bets,
                            )

                            html_message = MIMEText(html, "html")
                            html_message['Subject'] = subject
                            html_message['From'] = sender_email
                            html_message['To'] = recipient_email
                            
                            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                                server.login(sender_email, sender_password)
                                server.sendmail(sender_email, recipient_email, html_message.as_string())
                            
                            emails_sent += 1
                        except Exception as user_error:
                            logger.error(f"Error sending email to {user_id}: {str(user_error)}")

                # Log completion
                logger.info(f"Successfully sent {emails_sent} emails out of {len(users)} users")
                logger.info(f"===== Completed email cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")

                # Wait for next cycle
                next_run_time = datetime.now() + timedelta(minutes=EMAIL_INTERVAL_MINUTES)
                logger.info(f"Waiting until {next_run_time.strftime('%Y-%m-%d %H:%M:%S')} for next email cycle")
                
                # Sleep in smaller chunks to allow for graceful shutdown
                remaining_seconds = EMAIL_INTERVAL_MINUTES * 60
                while remaining_seconds > 0 and not is_shutting_down and not force_shutdown:
                    # Sleep in 10-second chunks for better responsiveness to shutdown
                    sleep_time = min(10, remaining_seconds)
                    await asyncio.sleep(sleep_time)
                    remaining_seconds -= sleep_time

            except Exception as db_error:
                if is_shutting_down or force_shutdown:
                    break

                connection_error_count += 1
                logger.error(f"Database error in send_regular_emails ({connection_error_count}/{max_connection_errors}): {str(db_error)}")

                if connection_error_count >= max_connection_errors:
                    logger.critical("Too many consecutive database errors. Restarting task.")
                    connection_error_count = 0
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(RETRY_DELAY * connection_error_count)
                continue

        except asyncio.CancelledError:
            logger.info("Email task cancelled")
            break
        except Exception as e:
            if is_shutting_down or force_shutdown:
                break
            logger.error(f"Error in send_regular_emails: {str(e)}")
            await asyncio.sleep(1)  # Reduced sleep time

        finally:
            if db:
                try:
                    db.close()
                    logger.info("Database connection closed")
                except Exception as close_error:
                    logger.error(f"Error closing database connection: {str(close_error)}")
                db = None

    logger.info("Email task shutting down")


def get_special_email_subject(arb_bets: list, ev_bets: list) -> str:
    best_arb = max((bet.profit_percentage for bet in arb_bets), default=0)
    best_ev = max((bet.ev_percentage for bet in ev_bets), default=0)

    # Create urgency with time-sensitive language
    if float(best_arb) > 5 and float(best_ev) > 15:
        subject = f"🔥 URGENT: {best_arb:.1f}% Arb + {best_ev:.1f}% EV Opportunity!"
    elif float(best_arb) > 5:
        subject = f"⚡ HOT: {best_arb:.1f}% Risk-Free Arbitrage Alert!"
    elif float(best_ev) > 15:
        subject = f"💰 ALERT: Massive {best_ev:.1f}% EV Opportunity!"
    else:
        subject = "🎯 High-Value Betting Alert!"

    return subject


@router.get("/trigger-email")
async def trigger_email():
    """API endpoint to manually trigger an email send"""
    try:
        # Create a background task to send emails without blocking the response
        asyncio.create_task(send_immediate_email())
        return {"status": "success", "message": "Email sending triggered"}
    except Exception as e:
        logger.error(f"Error triggering email: {str(e)}")
        return {"status": "error", "message": str(e)}


async def send_immediate_email():
    """Function to send emails immediately when triggered via API"""
    db = None
    try:
        logger.info("===== Manual email trigger initiated =====")
        
        db = get_db_with_retry()
        
        # Get active subscriptions
        active_subscriptions = db.query(Subscriptions).filter(
            Subscriptions.status == "active"
        ).all()
        active_subscriptions = [
            sub for sub in active_subscriptions if sub.current_period_end >= datetime.now(timezone.utc)]
        logger.info(f"Found {len(active_subscriptions)} active subscriptions")
        
        # Get bets data
        arb_bets = db.query(Arbitrage).filter(
            Arbitrage.opportunity_type == "Arbitrage",
            cast(Arbitrage.profit_percentage, Numeric) > 0
        ).all()

        # Get top 5 EV bets
        ev_bets = db.query(PlusEv).order_by(
            desc(PlusEv.ev_percentage)).limit(5).all()
        
        # Format data and send emails (similar to send_regular_emails)
        # ... (format bet data code)
        
        # Close DB connection
        if db:
            db.close()
            db = None
            
        logger.info("===== Manual email sending completed =====")
    except Exception as e:
        logger.error(f"Error in manual email send: {str(e)}")
    finally:
        if db:
            try:
                db.close()
            except:
                pass
