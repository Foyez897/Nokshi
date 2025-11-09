from datetime import datetime

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash
)

from config import Config
from models import db, Product, Order, AdminUser


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        ensure_default_admin()
        ensure_sample_products()

    # ---------- Helpers ----------

    def slugify(text: str) -> str:
        return (
            text.lower()
            .replace(" ", "-")
            .replace("/", "-")
            .replace("&", "and")
        )

    def get_cart():
        return session.get("cart", {})

    def save_cart(cart):
        session["cart"] = cart
        session.modified = True

    def is_admin():
        return bool(session.get("admin_id"))

    # ---------- Public ----------

    @app.route("/")
    def home():
        featured = Product.query.filter_by(is_featured=True, in_stock=True).limit(8).all()
        if not featured:
            featured = Product.query.filter_by(in_stock=True).limit(8).all()
        return render_template("home.html", products=featured)

    @app.route("/shop")
    def products_view():
        category = request.args.get("category")
        q = Product.query.filter_by(in_stock=True)
        if category:
            q = q.filter_by(category=category)
        products = q.order_by(Product.id.desc()).all()

        categories = [
            c[0]
            for c in db.session.query(Product.category)
            .filter_by(in_stock=True)
            .distinct()
            .all()
        ]

        return render_template(
            "products.html",
            products=products,
            categories=categories,
            selected_category=category,
        )

    @app.route("/product/<slug>")
    def product_detail(slug):
        product = Product.query.filter_by(slug=slug, in_stock=True).first_or_404()
        return render_template("product_detail.html", product=product)

    # ---------- Cart ----------

    @app.route("/add-to-cart/<int:product_id>", methods=["POST"])
    def add_to_cart(product_id):
        product = Product.query.get_or_404(product_id)
        if not product.in_stock:
            flash("This item is out of stock.", "warning")
            return redirect(url_for("products_view"))

        size = (request.form.get("size") or "").strip()
        qty = request.form.get("qty", "1")
        try:
            qty = int(qty)
        except ValueError:
            qty = 1
        if qty < 1:
            qty = 1

        cart = get_cart()
        key = f"{product_id}-{size or 'nosize'}"

        if key in cart:
            cart[key]["qty"] += qty
        else:
            cart[key] = {
                "id": product.id,
                "name": product.name,
                "price": float(product.price),
                "size": size,
                "qty": qty,
            }

        save_cart(cart)
        flash("Added to cart.", "success")
        return redirect(url_for("cart"))

    @app.route("/cart")
    def cart():
        cart = get_cart()
        total = sum(i["price"] * i["qty"] for i in cart.values())
        return render_template("cart.html", cart=cart, total=total)

    @app.route("/update-cart", methods=["POST"])
    def update_cart():
        cart = get_cart()
        for key in list(cart.keys()):
            qty_raw = request.form.get(f"qty_{key}", str(cart[key]["qty"]))
            try:
                qty = int(qty_raw)
            except ValueError:
                qty = cart[key]["qty"]

            if qty <= 0:
                del cart[key]
            else:
                cart[key]["qty"] = qty

        save_cart(cart)
        flash("Cart updated.", "info")
        return redirect(url_for("cart"))

    # ---------- Checkout ----------

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        cart = get_cart()
        if not cart:
            flash("Your cart is empty.", "warning")
            return redirect(url_for("products_view"))

        total = sum(i["price"] * i["qty"] for i in cart.values())

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            address = request.form.get("address", "").strip()

            if not all([name, email, phone, address]):
                flash("Please fill in all fields.", "danger")
                return redirect(url_for("checkout"))

            order = Order(
                customer_name=name,
                customer_email=email,
                customer_phone=phone,
                address=address,
                items=str(cart),
                total_price=total,
                created_at=datetime.utcnow(),
            )
            db.session.add(order)
            db.session.commit()

            session["cart"] = {}
            flash("Order placed! We will contact you to confirm.", "success")
            return redirect(url_for("order_success", order_id=order.id))

        return render_template("checkout.html", cart=cart, total=total)

    @app.route("/order-success/<int:order_id>")
    def order_success(order_id):
        order = Order.query.get_or_404(order_id)
        return render_template("order_success.html", order=order)

    # ---------- Admin Auth ----------

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()

            user = AdminUser.query.filter_by(username=username).first()
            if user and user.check_password(password):
                session["admin_id"] = user.id
                flash("Welcome, admin.", "success")
                return redirect(url_for("admin_dashboard"))

            flash("Invalid credentials.", "danger")

        return render_template("admin_login.html")

    @app.route("/admin/logout")
    def admin_logout():
        session.pop("admin_id", None)
        flash("Logged out.", "info")
        return redirect(url_for("admin_login"))

    # ---------- Admin Views ----------

    def require_admin_redirect():
        if not is_admin():
            return redirect(url_for("admin_login"))
        return None

    @app.route("/admin")
    def admin_dashboard():
        r = require_admin_redirect()
        if r:
            return r

        total_products = Product.query.count()
        total_orders = Order.query.count()
        pending_orders = Order.query.filter_by(status="Pending").count()
        recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()

        return render_template(
            "admin_dashboard.html",
            total_products=total_products,
            total_orders=total_orders,
            pending_orders=pending_orders,
            recent_orders=recent_orders,
        )

    @app.route("/admin/products", methods=["GET", "POST"])
    def admin_products():
        r = require_admin_redirect()
        if r:
            return r

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            price_raw = request.form.get("price", "0").strip()
            description = request.form.get("description", "").strip()
            image_url = request.form.get("image_url", "").strip()
            sizes = request.form.get("sizes", "").strip()
            is_featured = bool(request.form.get("is_featured"))

            try:
                price = float(price_raw)
            except ValueError:
                price = 0

            if not name or not category or price <= 0:
                flash("Name, category & valid price required.", "danger")
            else:
                slug = slugify(name)
                if Product.query.filter_by(slug=slug).first():
                    slug = f"{slug}-{int(datetime.utcnow().timestamp())}"

                p = Product(
                    name=name,
                    slug=slug,
                    category=category,
                    price=price,
                    description=description,
                    image_url=image_url,
                    sizes=sizes,
                    is_featured=is_featured,
                    in_stock=True,
                )
                db.session.add(p)
                db.session.commit()
                flash("Product added.", "success")

        products = Product.query.order_by(Product.id.desc()).all()
        return render_template("admin_products.html", products=products)

    @app.route("/admin/products/toggle/<int:product_id>")
    def admin_toggle_stock(product_id):
        r = require_admin_redirect()
        if r:
            return r

        product = Product.query.get_or_404(product_id)
        product.in_stock = not product.in_stock
        db.session.commit()
        flash("Stock status updated.", "info")
        return redirect(url_for("admin_products"))

    return app


def ensure_default_admin():
    if not AdminUser.query.filter_by(username="admin").first():
        admin = AdminUser(username="admin")
        admin.set_password("nokshi123")  # change after first login
        db.session.add(admin)
        db.session.commit()


def ensure_sample_products():
    if Product.query.count() == 0:
        demo = [
            ("Nokshi Handloom Saree", "Saree", 65.0),
            ("Embroidered Salwar Kameez", "Salwar", 55.0),
        ]
        for name, cat, price in demo:
            slug = name.lower().replace(" ", "-")
            p = Product(
                name=name,
                slug=slug,
                category=cat,
                price=price,
                description=f"Beautiful {cat.lower()} from Nokshi.",
                image_url="/static/images/placeholder.jpg",
                sizes="S,M,L,XL" if cat == "Salwar" else "",
                is_featured=True,
                in_stock=True,
            )
            db.session.add(p)
        db.session.commit()


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)