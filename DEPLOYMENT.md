# Maneendra General Stores V16.11 — Railway deployment

V16.11 uses SQLite on a Railway persistent volume and Brevo's HTTPS transactional email API.

## 1. GitHub
Push the project to a private GitHub repository. `.gitignore` excludes `store.db`, `.env`, virtual environments and caches.

## 2. Railway service
Create a Railway project from the GitHub repository.

## 3. Persistent volume
Attach a Railway Volume mounted at:

`/data`

The app automatically uses the Railway volume for its database and persistent uploads.

## 4. Railway Variables
Set:
- `ENVIRONMENT=production`
- `SESSION_SECRET=<long random secret>`
- `BREVO_API_KEY=<Brevo API key>`
- `BREVO_SENDER_EMAIL=www.maneendrageneralstores@gmail.com`
- `BREVO_SENDER_NAME=Maneendra General Stores`

The sender email must be verified in Brevo.

## 5. Deploy
`railway.json` provides the start command and `/health` healthcheck.

## 6. Public domain
Generate a Railway public domain under Service → Settings/Networking.

## 7. Production checks
- `/health` returns OK.
- Footer shows `Website version: V16.11`.
- Register/login and database persistence work after redeploy.
- Admin → Email Notifications shows **Brevo configured**.
- Send one test email.
- Test an order status email, delivery OTP and delivered-order PDF invoice attachment.
