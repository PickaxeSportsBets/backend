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


load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

email_password: str = os.environ.get("EMAIL_PASSWORD")

# Initialize Jinja2 environment
template_loader = jinja2.FileSystemLoader('src/email')
template_env = jinja2.Environment(loader=template_loader)

# Database retry settings
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


@backoff.on_exception(
    backoff.expo,
    (OperationalError, Exception),
    max_tries=MAX_RETRIES,
    max_time=300,
    giveup=lambda e: "connection refused" in str(e).lower()
)
def get_db_with_retry():
    """Get database session with retry logic"""
    try:
        db = next(get_db())
        # Test the connection
        db.execute(text("SELECT 1"))
        return db
    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise


def get_email_subject(arb_bets: list, ev_bets: list) -> str:
    # Get best profits for subject line
    best_arb = max((float(bet.profit_percentage)
                   for bet in arb_bets), default=0)
    best_ev = max((float(bet.ev_percentage) for bet in ev_bets), default=0)
    print("Best")
    print(best_arb, best_ev)

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


def is_within_operating_hours() -> bool:
    """Check if current time is within operating hours (8-11 PM EST)"""
    est = pytz.timezone('US/Eastern')
    current_time = datetime.now(est)

    # Define operating hours (20:00 - 23:00 EST)
    start_time = time(8, 0)  # 8 PM EST
    end_time = time(23, 0)    # 11 PM EST

    current_hour = current_time.time()

    return start_time <= current_hour <= end_time


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting email cron background task")
    task = asyncio.create_task(send_daily_email())
    yield
    logger.info("Shutting down email cron background task")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

router = APIRouter(tags=["emailCron"])


def format_bet_link(link: str) -> str:
    """Replace {state} placeholder with NY in bet links"""
    if link and isinstance(link, str):
        return link.replace("{state}", "NY")
    return link


async def send_daily_email():
    while True:
        try:
            # Check if we're within operating hours
            if not is_within_operating_hours():
                # Calculate time until next check
                est = pytz.timezone('US/Eastern')
                current_time = datetime.now(est)

                if current_time.time() < time(20, 0):  # Before 8 PM
                    next_run = current_time.replace(
                        hour=20, minute=0, second=0, microsecond=0)
                else:  # After 11 PM
                    next_run = current_time.replace(
                        hour=20, minute=0, second=0, microsecond=0)
                    next_run += timedelta(days=1)

                wait_seconds = (next_run - current_time).total_seconds()
                logger.info(
                    f"Outside operating hours. Waiting {wait_seconds/3600:.1f} hours until {next_run.strftime('%I:%M %p %Z')}")
                await asyncio.sleep(wait_seconds)
                continue

            # Get database session with retry
            db = None
            try:
                db = get_db_with_retry()

                # Get active subscriptions
                active_subscriptions = db.query(Subscriptions).filter(
                    Subscriptions.status == "active"
                ).all()
                active_subscriptions = [
                    sub for sub in active_subscriptions if sub.current_period_end >= datetime.now(timezone.utc)]
                logger.info(
                    f"Active subscriptions: {len(active_subscriptions)}")

                arb_bets = db.query(Arbitrage).filter(
                    Arbitrage.opportunity_type == "Arbitrage",
                    cast(Arbitrage.profit_percentage, Numeric) > 0
                ).all()

                # Get top 5 EV bets
                ev_bets = db.query(PlusEv).order_by(
                    desc(PlusEv.ev_percentage)).limit(5).all()

                # Format numeric values in arb_bets
                for bet in arb_bets:
                    bet.profit_percentage = float(bet.profit_percentage)
                    bet.team1_stake = float(
                        bet.team1_stake) if bet.team1_stake else 0
                    bet.team2_stake = float(
                        bet.team2_stake) if bet.team2_stake else 0
                    bet.team1_odds = float(
                        bet.team1_odds) if bet.team1_odds else 0
                    bet.team2_odds = float(
                        bet.team2_odds) if bet.team2_odds else 0
                    bet.team1_link = format_bet_link(bet.team1_link)
                    bet.team2_link = format_bet_link(bet.team2_link)

                # Format numeric values in ev_bets
                for bet in ev_bets:
                    bet.ev_percentage = float(bet.ev_percentage)
                    bet.odds = float(bet.odds) if bet.odds else 0
                    bet.fair_odds = float(
                        bet.fair_odds) if bet.fair_odds else 0
                    bet.link = format_bet_link(bet.link)

                logger.info("Sending hourly email")
                users = set(
                    subscription.clerk_user_id for subscription in active_subscriptions)
                info = []

                with Clerk(bearer_auth=os.environ.get("CLERK_SECRET_KEY")) as clerk:
                    for user_id in users:
                        try:
                            user = clerk.users.get(user_id=user_id)
                            name = user.first_name + " " + user.last_name
                            email = user.email_addresses[0].email_address
                            logger.info(f"Name: {name}, Email: {email}")
                            sender_email = "pickaxebets@gmail.com"
                            sender_password = email_password
                            recipient_email = email
                            template = template_env.get_template(
                                'template.html')
                            html = template.render(
                                email=email,
                                name=name,
                                arbBets=arb_bets,
                                evBets=ev_bets,
                            )

                            subject = get_email_subject(arb_bets, ev_bets)
                            html_message = MIMEText(html, "html")
                            html_message['Subject'] = subject
                            html_message['From'] = sender_email
                            html_message['To'] = recipient_email
                            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                                server.login(sender_email, sender_password)
                                server.sendmail(
                                    sender_email, recipient_email, html_message.as_string())
                        except Exception as user_error:
                            logger.error(
                                f"Error sending email to {user_id}: {str(user_error)}")

                # Wait for 1 hour before next check
                logger.info(
                    "Emails sent successfully. Waiting 1 hour before next check.")
                logger.info(f"Emails sent to {len(users)} users")
                await asyncio.sleep(3600)

            except Exception as db_error:
                logger.error(
                    f"Database error in send_daily_email: {str(db_error)}")
                await asyncio.sleep(RETRY_DELAY)
                continue

            finally:
                if db:
                    try:
                        db.close()
                    except Exception as close_error:
                        logger.error(
                            f"Error closing database connection: {str(close_error)}")

        except Exception as e:
            logger.error(f"Error in send_daily_email: {str(e)}")
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error


async def send_special_email():
    while True:
        try:
            # Check if we're within operating hours
            if not is_within_operating_hours():
                # Calculate time until next check
                est = pytz.timezone('US/Eastern')
                current_time = datetime.now(est)

                if current_time.time() < time(20, 0):  # Before 8 PM
                    next_run = current_time.replace(
                        hour=20, minute=0, second=0, microsecond=0)
                else:  # After 11 PM
                    next_run = current_time.replace(
                        hour=20, minute=0, second=0, microsecond=0)
                    next_run += timedelta(days=1)

                wait_seconds = (next_run - current_time).total_seconds()
                logger.info(
                    f"Outside operating hours. Waiting {wait_seconds/3600:.1f} hours until {next_run.strftime('%I:%M %p %Z')}")
                await asyncio.sleep(wait_seconds)
                continue

            # Get database session with retry
            db = None
            try:
                db = get_db_with_retry()

                # Get active subscriptions
                active_subscriptions = db.query(Subscriptions).filter(
                    Subscriptions.status == "active"
                ).all()
                active_subscriptions = [
                    sub for sub in active_subscriptions if sub.current_period_end >= datetime.now(timezone.utc)]
                logger.info(
                    f"Active subscriptions: {len(active_subscriptions)}")

                # Get high-value arbitrage bets
                arb_bets = db.query(Arbitrage).filter(
                    Arbitrage.opportunity_type == "Arbitrage",
                    cast(Arbitrage.profit_percentage, Numeric) > 3
                ).all()

                # Get high-value EV bets
                ev_bets = db.query(PlusEv).filter(
                    cast(PlusEv.ev_percentage, Numeric) > 10
                ).order_by(desc(PlusEv.ev_percentage)).all()

                if len(arb_bets) == 0 and len(ev_bets) == 0:
                    logger.info(
                        "No special bets found. Waiting 10 minutes before next check.")
                    await asyncio.sleep(600)
                    continue

                # Format links in arb_bets
                for bet in arb_bets:
                    bet.team1_link = format_bet_link(bet.team1_link)
                    bet.team2_link = format_bet_link(bet.team2_link)

                # Format links in ev_bets
                for bet in ev_bets:
                    bet.link = format_bet_link(bet.link)

                logger.info("Sending hourly email")
                users = set(
                    subscription.clerk_user_id for subscription in active_subscriptions)
                info = []

                with Clerk(bearer_auth=os.environ.get("CLERK_SECRET_KEY")) as clerk:
                    for user_id in users:
                        try:
                            user = clerk.users.get(user_id=user_id)
                            name = user.first_name + " " + user.last_name
                            email = user.email_addresses[0].email_address
                            logger.info(f"Name: {name}, Email: {email}")
                            sender_email = "pickaxebets@gmail.com"
                            sender_password = email_password
                            recipient_email = email
                            template = template_env.get_template(
                                'specialEmail.html')
                            html = template.render(
                                email=email,
                                name=name,
                                arbBets=arb_bets,
                                evBets=ev_bets,
                            )

                            subject = get_special_email_subject(
                                arb_bets, ev_bets)
                            html_message = MIMEText(html, "html")
                            html_message['Subject'] = subject
                            html_message['From'] = sender_email
                            html_message['To'] = recipient_email
                            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                                server.login(sender_email, sender_password)
                                server.sendmail(
                                    sender_email, recipient_email, html_message.as_string())
                        except Exception as user_error:
                            logger.error(
                                f"Error sending email to {user_id}: {str(user_error)}")

                # Wait for 10 minutes before next check
                logger.info(
                    "Emails sent successfully. Waiting 10 minutes before next check.")
                await asyncio.sleep(600)

            except Exception as db_error:
                logger.error(
                    f"Database error in send_special_email: {str(db_error)}")
                await asyncio.sleep(RETRY_DELAY)
                continue

            finally:
                if db:
                    try:
                        db.close()
                    except Exception as close_error:
                        logger.error(
                            f"Error closing database connection: {str(close_error)}")

        except Exception as e:
            logger.error(f"Error in send_special_email: {str(e)}")
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error


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
