from fastapi import APIRouter
from supabase import create_client, Client
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


load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
email_password: str = os.environ.get("EMAIL_PASSWORD")
supabase: Client = create_client(url, key)

# Initialize Jinja2 environment
template_loader = jinja2.FileSystemLoader('src/email')
template_env = jinja2.Environment(loader=template_loader)


def get_email_subject(arb_bets: list, ev_bets: list) -> str:
    # Get best profits for subject line
    best_arb = max((bet["profit_percentage"] for bet in arb_bets), default=0)
    best_ev = max((bet["ev_percentage"] for bet in ev_bets), default=0)

    # Convert to float first to avoid string formatting issues
    best_arb_float = float(best_arb)
    best_ev_float = float(best_ev)

    # Base options for variety
    if best_arb_float > 2 and best_ev_float > 4:
        subject = "🔥 Hot Alert! {:.1f}% Arb + {:.1f}% EV".format(
            best_arb_float, best_ev_float)
    elif best_arb_float > 2:
        subject = "💰 Profit Alert! {:.1f}% Risk-Free Arb".format(
            best_arb_float)
    elif best_ev_float > 4:
        subject = "⚡ Value Alert! {:.1f}% EV Play".format(best_ev_float)
    else:
        subject = "🎯 Fresh Betting Picks"

    # Add secondary opportunities
    if best_arb_float > 0 and "Arb" not in subject:
        subject += " | {:.1f}% Arb".format(best_arb_float)
    if best_ev_float > 0 and "EV" not in subject:
        subject += " | {:.1f}% EV".format(best_ev_float)

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
                logger.info(f"Outside operating hours. Waiting {
                            wait_seconds/3600:.1f} hours until {next_run.strftime('%I:%M %p %Z')}")
                await asyncio.sleep(wait_seconds)
                continue

            # Rest of your existing email sending code...
            active_subscriptions = supabase.table("subscriptions").select(
                "*").eq("status", "active").lt("current_period_end", datetime.now(timezone.utc)).execute()
            arbBets = supabase.table("arbitrage").select(
                "*").eq("opportunity_type", "Arbitrage").gt("profit_percentage", 1).execute()
            evBets = supabase.table("plus_ev").select(
                "*").order('ev_percentage', desc=True).limit(5).execute()

            # Format links in arbBets
            for bet in arbBets.data:
                bet['team1_link'] = format_bet_link(bet.get('team1_link', ''))
                bet['team2_link'] = format_bet_link(bet.get('team2_link', ''))

            # Format links in evBets
            for bet in evBets.data:
                bet['link'] = format_bet_link(bet.get('link', ''))

            logger.info("Sending hourly email")
            users = set(subscription["clerk_user_id"]
                        for subscription in active_subscriptions.data)
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
                        template = template_env.get_template('template.html')
                        html = template.render(
                            email=email,
                            name=name,
                            arbBets=arbBets.data,
                            evBets=evBets.data,
                        )
                        print("Subject")

                        subject = get_email_subject(
                            arbBets.data, evBets.data)
                        print(subject)
                        print(type(subject))
                        html_message = MIMEText(html, "html")
                        html_message['Subject'] = subject
                        html_message['From'] = sender_email
                        html_message['To'] = recipient_email
                        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                            server.login(sender_email, sender_password)
                            server.sendmail(
                                sender_email, recipient_email, html_message.as_string())
                    except Exception as user_error:
                        logger.error(f"Error sending email to {
                                     user_id}: {str(user_error)}")

            # Wait for 1 hour before next check
            logger.info(
                "Emails sent successfully. Waiting 1 hour before next check.")
            await asyncio.sleep(3600)

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
                logger.info(f"Outside operating hours. Waiting {
                            wait_seconds/3600:.1f} hours until {next_run.strftime('%I:%M %p %Z')}")
                await asyncio.sleep(wait_seconds)
                continue

            # Rest of your existing email sending code...
            active_subscriptions = supabase.table("subscriptions").select(
                "*").eq("status", "active").lt("current_period_end", datetime.now(timezone.utc)).execute()
            arbBets = supabase.table("arbitrage").select(
                "*").eq("opportunity_type", "Arbitrage").gt("profit_percentage", 3).execute()
            evBets = supabase.table("plus_ev").select(
                "*").order('ev_percentage', desc=True).gt("ev_percentage", 10).execute()
            if len(arbBets.data) == 0 and len(evBets.data) == 0:
                logger.info(
                    "No special bets found. Waiting 10 minutes before next check.")
                await asyncio.sleep(600)
                continue

            # Format links in arbBets
            for bet in arbBets.data:
                bet['team1_link'] = format_bet_link(bet.get('team1_link', ''))
                bet['team2_link'] = format_bet_link(bet.get('team2_link', ''))

            # Format links in evBets
            for bet in evBets.data:
                bet['link'] = format_bet_link(bet.get('link', ''))

            logger.info("Sending hourly email")
            users = set(subscription["clerk_user_id"]
                        for subscription in active_subscriptions.data)
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
                            arbBets=arbBets.data,
                            evBets=evBets.data,
                        )

                        subject = get_special_email_subject(
                            arbBets.data, evBets.data)
                        html_message = MIMEText(html, "html")
                        html_message['Subject'] = subject
                        html_message['From'] = sender_email
                        html_message['To'] = recipient_email
                        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                            server.login(sender_email, sender_password)
                            server.sendmail(
                                sender_email, recipient_email, html_message.as_string())
                    except Exception as user_error:
                        logger.error(f"Error sending email to {
                            user_id}: {str(user_error)}")

            # Wait for 1 hour before next check
            logger.info(
                "Emails sent successfully. Waiting 10 minutse before next check.")
            await asyncio.sleep(600)

        except Exception as e:
            logger.error(f"Error in send_daily_email: {str(e)}")


def get_special_email_subject(arb_bets: list, ev_bets: list) -> str:
    best_arb = max((bet["profit_percentage"] for bet in arb_bets), default=0)
    best_ev = max((bet["ev_percentage"] for bet in ev_bets), default=0)

    # Create urgency with time-sensitive language
    if float(best_arb) > 5 and float(best_ev) > 15:
        subject = f"🔥 URGENT: {
            best_arb:.1f}% Arb + {best_ev:.1f}% EV Opportunity!"
    elif float(best_arb) > 5:
        subject = f"⚡ HOT: {best_arb:.1f}% Risk-Free Arbitrage Alert!"
    elif float(best_ev) > 15:
        subject = f"💰 ALERT: Massive {best_ev:.1f}% EV Opportunity!"
    else:
        subject = "🎯 High-Value Betting Alert!"

    return subject
