"""The semantic layer: every metric and dimension the investigator may use, defined once.

All metrics are computed over the sessions table (one row per session). A rate metric is a ratio of two
session counts, so it can be split into "mix" (which segments the sessions came from) and "rate" (how each
segment converted). The model never writes SQL: it asks for these metrics by name.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    num: str                 # SQL aggregate for the numerator
    den: str | None = None   # SQL aggregate for the denominator (None: a plain total)
    fmt: str = "count"       # count | pct | usd
    parent: str | None = None  # the session subset the rate is taken over (for funnel steps)

    @property
    def is_rate(self) -> bool:
        return self.den is not None


S = "count(*)"
HAS = "count(*) FILTER (WHERE {} > 0)"

METRICS = {m.name: m for m in [
    Metric("sessions", "Sessions", S),
    Metric("users", "Users", "count(DISTINCT user_pseudo_id)"),
    Metric("orders", "Orders", "sum(transactions)"),
    Metric("revenue", "Revenue", "sum(revenue)", fmt="usd"),
    Metric("conversion_rate", "Conversion rate (sessions with a purchase)", HAS.format("purchase"), S, "pct"),
    Metric("view_item_rate", "Product-view rate", HAS.format("view_item"), S, "pct"),
    Metric("add_to_cart_rate", "Add-to-cart rate (of sessions)", HAS.format("add_to_cart"), S, "pct"),
    Metric("cart_rate", "Add-to-cart after a product view", HAS.format("add_to_cart"),
           HAS.format("view_item"), "pct", parent="view_item"),
    Metric("checkout_start_rate", "Checkout start after add-to-cart", HAS.format("begin_checkout"),
           HAS.format("add_to_cart"), "pct", parent="add_to_cart"),
    Metric("checkout_completion_rate", "Purchase after checkout start", HAS.format("purchase"),
           HAS.format("begin_checkout"), "pct", parent="begin_checkout"),
    Metric("aov", "Average order value", "sum(revenue)", "nullif(sum(transactions), 0)", "usd"),
    Metric("revenue_per_session", "Revenue per session", "sum(revenue)", S, "usd"),
]}

# The funnel, in order: each step's rate is taken over sessions that reached the previous step.
FUNNEL = ["view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate"]

DIMENSIONS = {
    "device": "Device category",
    "os": "Operating system",
    "browser": "Browser",
    "country": "Country",
    "medium": "Traffic medium",
    "source": "Traffic source",
    "landing": "Landing page section",
    "user_type": "New or returning user",
}

# Placeholder values in the obfuscated export: kept as their own segment, never named as a cause on their own.
PLACEHOLDERS = {"<Other>", "(not set)", "(data deleted)", "", None}
