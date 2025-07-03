from flask import Flask, render_template, request, redirect, url_for, flash, session 
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import boto3
import smtplib
import logging
import uuid
from email.mime.text import MIMEText
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY="homemade-secret-key",
    THEME_COLOR="#ffb6c1"
)

# -------------------- Logger Setup --------------------
log_folder = 'logs'
os.makedirs(log_folder, exist_ok=True)
log_file = os.path.join(log_folder, 'app.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# -------------------- AWS Setup --------------------
AWS_REGION = 'ap-south-1'
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:905418361023:Travelgo'

dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
orders_table = dynamodb.Table('PickleOrders')
users_table = dynamodb.Table('users')
sns = boto3.client('sns', region_name=AWS_REGION)

#Email settings (loaded securely from .env)
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USER = os.getenv('EMAIL_USER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')


@app.context_processor
def inject_theme():
    return {"color": app.config["THEME_COLOR"], "year": datetime.now().year}

# ---------- Updated Product Inventory ----------
products = {
    "mango": {"name": "Mango Pickle", "price": 249, "stock": 10, "image": "mango.jpg"},
    "lemon": {"name": "Lemon Pickle", "price": 199, "stock": 8, "image": "lemon.jpg"},
    "gongura": {"name": "Gongura Pickle", "price": 229, "stock": 7, "image": "gongura.jpg"},
    "chicken": {"name": "Chicken Pickle", "price": 349, "stock": 12, "image": "chicken.jpg"},
    "fish": {"name": "Fish Pickle", "price": 329, "stock": 9, "image": "fish.jpg"},
    "prawns": {"name": "Prawns Pickle", "price": 399, "stock": 6, "image": "prawns.jpg"},
    "murukulu": {"name": "Murukulu", "price": 149, "stock": 20, "image": "murukulu.jpg"},
    "nippattu": {"name": "Nippattu", "price": 129, "stock": 15, "image": "nippattu.jpg"},
    "hot_maida_biscuit": {"name": "Hot Maida Biscuit", "price": 109, "stock": 25, "image": "hot_maida_biscuit.jpg"},
}

def get_products(prefix=None):
    if not prefix:
        return products
    return {k: v for k, v in products.items() if k.startswith(prefix)}

# ------------- Helper Functions -------------
def send_order_email(to_email, order_summary):
    try:
        msg = MIMEText(order_summary)
        msg['Subject'] = 'Your Order Confirmation'
        msg['From'] = EMAIL_USER
        msg['To'] = to_email

        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        logger.info("Order email sent to %s", to_email)
    except Exception as e:
        logger.error("Failed to send email: %s", e)

def save_order_to_dynamodb(order_data):
    try:
        orders_table.put_item(Item=order_data)
        logger.info("Order saved to DynamoDB: %s", order_data['order_id'])
    except Exception as e:
        logger.error("DynamoDB error: %s", e)

def send_sns_notification(message, topic_arn=SNS_TOPIC_ARN):
    try:
        if topic_arn:
            sns.publish(TopicArn=topic_arn, Message=message)
            logger.info(f"SNS message published to topic {topic_arn}")
    except Exception as e:
        logger.error("SNS send failed: %s", e)

# ------------- Routes -------------
@app.route("/")
def home():
    best = dict(list(get_products().items())[:6])
    return render_template("index.html", items=best)

@app.route("/veg")
def veg():
    return render_template("veg.html", items={
        k: v for k, v in products.items() if k in ["mango", "lemon", "gongura"]
    })

@app.route("/nonveg")
def nonveg():
    return render_template("nonveg.html", items={
        k: v for k, v in products.items() if k in ["chicken", "fish", "prawns"]
    })

@app.route("/snacks")
def snacks():
    return render_template("snacks.html", items={
        k: v for k, v in products.items() if k in ["murukulu", "nippattu", "hot_maida_biscuit"]
    })

@app.route("/cart")
def cart():
    items, total = {}, 0
    if session.get("cart"):
        for pid, qty in session["cart"].items():
            if pid in products:
                items[pid] = {**products[pid], "qty": qty}
                total += products[pid]["price"] * qty
    return render_template("cart.html", items=items, total=total)

@app.route("/add/<pid>")
def add_to_cart(pid):
    if pid not in products:
        flash("Invalid product", "danger")
        return redirect(request.referrer or url_for("home"))
    if products[pid]["stock"] <= 0:
        flash("Out of stock", "warning")
    else:
        products[pid]["stock"] -= 1
        cart = session.setdefault("cart", {})
        cart[pid] = cart.get(pid, 0) + 1
        session.modified = True
        flash("Added to cart!", "success")
    return redirect(request.referrer or url_for("home"))

@app.route("/clear-cart")
def clear_cart():
    session.pop("cart", None)
    flash("Cart cleared", "info")
    return redirect(url_for("home"))

@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart = session.get("cart", {})
    if not cart:
        flash("Cart is empty", "warning")
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        address = request.form["address"]
        order_id = str(uuid.uuid4())
        order_time = datetime.now().isoformat()

        items = [
            {"product": pid, "price": products[pid]["price"], "quantity": qty}
            for pid, qty in cart.items()
        ]
        total = sum(p["price"] * p["quantity"] for p in items)

        order_data = {
            "order_id": order_id,
            "name": name,
            "email": email,
            "address": address,
            "order_time": order_time,
            "items": items,
            "total": total
        }

        save_order_to_dynamodb(order_data)

        summary = f"Order ID: {order_id}\nName: {name}\nTotal: ₹{total}\n\nThank you for your order!"
        send_order_email(email, summary)
        send_sns_notification(f"New order placed: {order_id}")

        session.pop("cart", None)
        flash("Order placed successfully!", "success")
        return redirect(url_for("success"))
    return render_template("checkout.html")

@app.route("/success")
def success():
    return render_template("success.html")

users = {}

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        pwd = request.form["password"]
        confirm = request.form["confirm"]
        if pwd != confirm:
            flash("Passwords don’t match", "warning")
            return redirect(url_for("signup"))
        if email in users:
            flash("User exists, log in", "info")
            return redirect(url_for("login"))
        users[email] = {"hash": generate_password_hash(pwd)}
        flash("Signup complete — please log in", "success")
        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        pwd = request.form["password"]
        user = users.get(email)
        if user and check_password_hash(user["hash"], pwd):
            session["user"] = email
            flash("Logged in!", "success")
            return redirect(url_for("home"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for("home"))

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        flash("Message sent!", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500

if __name__ == "__main__":
    app.run(debug=True)
