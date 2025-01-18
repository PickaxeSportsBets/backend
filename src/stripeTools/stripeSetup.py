import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from datetime import datetime
load_dotenv()


stripe.api_key = os.getenv("NEXT_PUBLIC_STRIPE_TEST_SECRET_KEY")


def get_or_create_product(name, description, metadata):
    # List existing products and check for match
    existing_products = stripe.Product.list(active=True)
    for product in existing_products:
        if product.name == name:
            print(f"Product '{name}' already exists with ID: {product.id}")
            return product

    # Create new product if not found
    print(f"Creating new product: {name}")
    return stripe.Product.create(
        name=name,
        description=description,
        metadata=metadata
    )


def get_or_create_price(product_id, unit_amount, currency, recurring, nickname):
    # List existing prices for the product
    existing_prices = stripe.Price.list(product=product_id, active=True)
    for price in existing_prices:
        if (price.unit_amount == unit_amount and
            price.recurring.interval == recurring["interval"] and
                price.nickname == nickname):
            print(f"Price '{nickname}' already exists with ID: {price.id}")
            return price

    # Create new price if not found
    print(f"Creating new price: {nickname}")
    return stripe.Price.create(
        product=product_id,
        unit_amount=unit_amount,
        currency=currency,
        recurring=recurring,
        nickname=nickname
    )


def setup_products_and_prices():
    try:
        # Email Picks Plan
        email_plan = get_or_create_product(
            name="Email Picks Plan",
            description="Handpicked arbitrage and EV opportunities sent directly to your email",
            metadata={
                "features": "Daily picks,Weekly summary,Email delivery",
                "plan_type": "email_picks"
            }
        )

        # Create/get prices for Email Picks Plan
        email_monthly = get_or_create_price(
            product_id=email_plan.id,
            unit_amount=5000,
            currency="usd",
            recurring={"interval": "month"},
            nickname="Monthly Email Picks"
        )

        email_weekly = get_or_create_price(
            product_id=email_plan.id,
            unit_amount=2500,
            currency="usd",
            recurring={"interval": "week"},
            nickname="Weekly Email Picks"
        )

        email_daily = get_or_create_price(
            product_id=email_plan.id,
            unit_amount=1000,
            currency="usd",
            recurring={"interval": "day"},
            nickname="Daily Email Picks"
        )

        # Software Plan
        software_plan = get_or_create_product(
            name="Software Plan (Standard)",
            description="Full access to betting opportunities platform",
            metadata={
                "features": "Full software access,Unlimited opportunities,Personalized alerts",
                "plan_type": "software"
            }
        )

        # Create/get price for Software Plan
        software_monthly = get_or_create_price(
            product_id=software_plan.id,
            unit_amount=7500,
            currency="usd",
            recurring={"interval": "month"},
            nickname="Monthly Software Access"
        )

        # Save IDs to a file
        with open(f'stripe_products_{datetime.now().strftime("%Y%m%d")}.txt', 'w') as f:
            f.write(f"""
Email Picks Plan ID: {email_plan.id}
Monthly Price ID: {email_monthly.id}
Weekly Price ID: {email_weekly.id}
Daily Price ID: {email_daily.id}

Software Plan ID: {software_plan.id}
Monthly Software Price ID: {software_monthly.id}
            """)

        print("\nAll products and prices are set up!")

    except stripe.error.StripeError as e:
        print("Error:", e)


if __name__ == "__main__":
    setup_products_and_prices()
