# Maneendra General Stores — Full-Stack V16.11

V16.11 keeps the V16.3 deployment-ready website and switches transactional customer email from Gmail SMTP to the **Brevo HTTPS API**. This is suitable for Railway deployments where SMTP is unavailable/restricted.

## Email features using Brevo
- Order confirmation/status emails
- UPI payment confirmation emails
- Delivery OTP emails
- Cancellation/return emails
- Final delivered-order PDF invoice attachment
- Admin test email
- Existing email logs remain available

## Local run
```cmd
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```
Open `http://127.0.0.1:8000` and confirm **Website version: V16.11**.

## Brevo setup
1. Create a Brevo account.
2. Verify the email address you will use as the sender.
3. Create a Brevo API key.
4. Copy `.env.example` to `.env` locally and set `BREVO_API_KEY`.
5. Keep `.env` private; it is excluded by `.gitignore`.
6. In Admin → Email Notifications, confirm the verified sender email and use **Send Test**.

For Railway, add `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, and `BREVO_SENDER_NAME` under Service → Variables rather than uploading `.env`.

## Keeping your existing local data
If you already have customers/orders in V16.3, stop the old server and copy only `store.db` from your V16.3 folder into V16.11 before the first V16.11 start. V16.11 migrates the existing database automatically.


## V16.11 UI fix
The Admin/My Account modal now always opens above the sticky site header, stays fully inside the browser viewport, scrolls internally, and becomes full-screen on mobile. Admin tabs remain accessible while scrolling.


## V16.11 admin page-fit fix
The Admin modal is now a viewport-fit dashboard. Header, summary cards and tabs remain inside the screen; only the active section scrolls. Store Details uses a two-column desktop form and Email Notifications uses both columns of the modal.


## V16.11 UI layering fix
The cart drawer and all dialogs now render above the sticky header. Cart content scrolls internally and its subtotal/checkout footer remains accessible within the viewport. Modal and cart layers were audited so sticky navigation and floating action buttons cannot cover active surfaces.

## V16.11 stale-session cart fix

V16.11 fixes a SQLite `FOREIGN KEY constraint failed` error that could occur when the browser kept an old login session after `store.db` was copied/replaced between versions. The app now validates the session's `user_id` before creating a cart. If that user no longer exists, the stale login is cleared automatically and a guest cart is used instead.



## V16.11 return/cancel dialog fix

Return and Cancel now open immediately as nested dialogs above My Account. The customer account remains open underneath, and closing the request restores the same order/history position.
