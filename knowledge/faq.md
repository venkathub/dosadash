---
title: Frequently Asked Questions
doc_type: faq
tags: [ordering, delivery, payment, telegram, account, faq]
---

# Frequently Asked Questions

## Ordering

### How do I place an order?

Three ways, all against the same menu and account:

1. **Website** — browse the menu, add to cart, and check out.
2. **Telegram** — chat with the DosaDash bot; link it to your account once
   and order by message (voice notes arrive in a later release).
3. **AI assistant** — ask in plain language ("2 masala dosas and a filter
   coffee"); the assistant builds an order draft you confirm before anything
   is placed. Nothing is ever ordered without your explicit confirmation.

### Do I need an account?

Yes — sign in with your mobile number. We send a one-time password (OTP);
there are no passwords to remember. In the demo deployment the OTP is shown
in an on-screen banner or delivered via Telegram DM.

### Can I customise a dish?

Spice level can be adjusted on most cooked-to-order dishes (dosas, curries,
biryanis) — mention it in the item notes. Common requests: "less spicy",
"no onion", "chutney/masala packed separately", "ghee on the side".

### What do the veg / vegan / Jain labels mean?

`veg` = vegetarian (may contain dairy). `vegan` = no meat, egg, or dairy.
`Jain-friendly` = vegetarian and prepared without onion or garlic. Full
per-dish detail is in the Allergen & Dietary Guide.

### An item shows as unavailable — why?

Items are marked unavailable ("86'd") when the kitchen runs out of an
ingredient, or outside an item's scheduled serving window. Unavailable items
cannot be ordered — by any channel, including the AI assistant — until the
kitchen re-enables them.

### Why can't I order a dosa at lunch?

Because we cook like a proper Tamil tiffin centre: every dish has a serving
window, and the dosa griddle rests at lunch. Dosas, idlis, vadas, uttapams,
millet tiffin, and tiffin dishes like idiyappam and appam serve at breakfast
(6–11:30 AM IST) and again at dinner (5–10 PM IST). Between 11:30 AM and
5 PM the kitchen turns to its lunch counters — rice varieties, biryani,
Chettinad curries, and mess specials (11:30 AM–10 PM). Pongals are
mornings-only (6 AM–12 noon), the Mini Tiffin is breakfast-only, veg meals
run in two sittings, and the Non-Veg Mess Meals are lunch-only (11:30 AM–
4 PM). Sweets and beverages serve all day. Each dish's window is listed in
its menu guide, and the site simply hides dishes outside their window.

### What are your hours?

Kitchen hours are set by the restaurant and shown on the site. Outside
hours, or when the kitchen is temporarily paused, checkout is disabled and
the assistant will say so rather than take an order it can't fulfil.

## Delivery

### Where do you deliver?

Delivery is limited to a configured list of Chennai pincodes (currently the
600001, 600002, 600004, and 600017 areas). If your address's pincode isn't
served, checkout will tell you before payment.

### How long does delivery take?

Prep time is shown per dish (idli ~10 min; dosas 15–20 min; biryani 25–35
min) plus travel time. You can watch live status: PLACED → CONFIRMED →
COOKING → READY → OUT FOR DELIVERY → DELIVERED.

### Can I schedule an order for later?

Not yet — orders go straight to the kitchen when placed.

## Payments

### What payment methods do you accept?

Payments run through Razorpay (cards, UPI, netbanking, wallets). This is a
demo deployment in Razorpay TEST mode — no real money moves; use Razorpay
test cards/UPI IDs from the demo page.

### Is GST included?

GST of 5% (food rate) is added at checkout and itemised on your bill.
Menu prices are listed before GST.

## Telegram & account

### How do I link Telegram?

Open the bot from the link on your account page (a deep link ties the chat
to your signed-in account), or use the Telegram Login Widget on the site.
Once linked you can order, get OTPs, and receive order updates in DM.
You can unlink at any time from account settings.

### How is my data handled?

We store your phone number, addresses, order history, and preferences
(diet, allergens, spice, language) to personalise service. Phone numbers
are redacted before any text reaches an AI model or a log line.
