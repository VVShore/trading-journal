# Deployment Guide — The Craftsman's Mirror (Whop Hosted)

## Overview
Stack: FastAPI + PostgreSQL on Railway, Groq AI (free), Whop auth
Cost: $0 for first few months under 50 users

---

## Step 1: Get your API keys (all free)

### A. Groq API Key (for AI analysis)
1. Go to https://console.groq.com
2. Create a free account
3. Go to API Keys → Create API Key
4. Copy it — you'll need it in Step 3

### B. Whop API Key
1. Go to https://whop.com/dashboard
2. Developer Settings → API Keys
3. Create a new key
4. Copy it — you'll need it in Step 3

### C. JWT Secret (generate yourself)
Run this in any terminal:
```
python -c "import secrets; print(secrets.token_hex(32))"
```
Copy the output — this is your JWT_SECRET.

---

## Step 2: Deploy to Railway

### 2a. Create Railway account
Go to https://railway.app and sign up with GitHub.

### 2b. Create a new project
1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Connect your GitHub account if not already done
4. Push this folder to a new GitHub repo first:
   ```
   git init
   git add .
   git commit -m "initial"
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```
5. Select that repo in Railway

### 2c. Add PostgreSQL database
1. In your Railway project, click "+ New"
2. Select "Database" → "Add PostgreSQL"
3. Railway automatically sets DATABASE_URL in your environment — you do NOT need to copy this manually

### 2d. Set environment variables
In Railway, go to your app service → Variables tab → Add these:

| Variable      | Value                        | Notes                         |
|---------------|------------------------------|-------------------------------|
| DATABASE_URL  | (auto-set by Railway)        | Don't touch this              |
| WHOP_API_KEY  | your_whop_api_key            | From Step 1B                  |
| JWT_SECRET    | your_generated_secret        | From Step 1C                  |
| GROQ_API_KEY  | your_groq_api_key            | From Step 1A                  |

### 2e. Deploy
Railway auto-deploys when you push to GitHub. First deploy takes ~2 minutes.
Watch the deploy logs for any errors.

Your app URL will be something like: https://your-app-name.railway.app

---

## Step 3: Set up Whop webapp

1. Go to Whop dashboard → your product
2. Go to Settings or "Apps" section
3. Add a new App / Webapp
4. Set the URL to: `https://your-app-name.railway.app`
5. Toggle "Embed webapp" ON if you want it in an iframe, or OFF to open as a new tab

### How Whop passes the user token
When a member accesses your webapp through Whop, Whop automatically appends
`?token=USER_TOKEN` to your URL. The journal reads this token, verifies it
with Whop's API, and creates a session. Members never see this — it's invisible.

---

## Step 4: Test

1. Visit your Railway URL directly — you should see "Access this journal through your Whop membership"
2. Open it through your Whop product — you should see the loading spinner, then the journal
3. Log a test trade to verify database is working

### Test without Whop (local dev)
Set environment variable `DEV_MODE=true` (locally or in Railway temporarily).
This bypasses Whop verification and uses `dev_user_001` as the user ID.
**Remove DEV_MODE before going live.**

---

## Known limitations (MVP)

### Images are ephemeral
Uploaded screenshots are stored on Railway's filesystem.
When the app restarts (deployments, crashes), images are deleted.
The trade record remains — just the image is lost.

**Fix when you're ready to scale:** Add Cloudinary (free tier: 25GB storage)
- Sign up at cloudinary.com
- Install: `pip install cloudinary`
- Change `log_trade()` to upload to Cloudinary instead of local disk
- Store the Cloudinary URL as `image_path`

### Read Chart / Screenshot require local setup
These features use `chart_bridge.js` which runs on the user's machine.
When the journal is hosted on Railway, the bridge runs locally and talks
to the hosted backend. Each user who wants these features needs to:
1. Install Node.js
2. Install tradingview-mcp
3. Run launch_chrome_debug.bat
4. The journal (on Railway) still works for everything else without this

---

## Updating the app
Just push to GitHub — Railway auto-deploys.

```
git add .
git commit -m "update"
git push
```

---

## Environment variable reference

| Variable      | Required | Description                              |
|---------------|----------|------------------------------------------|
| DATABASE_URL  | Yes      | Auto-set by Railway PostgreSQL plugin    |
| WHOP_API_KEY  | Yes      | Your Whop API key for token verification |
| JWT_SECRET    | Yes      | Random secret for signing session tokens |
| GROQ_API_KEY  | Yes      | Groq API key for AI analysis (free)      |
| DEV_MODE      | No       | Set "true" to bypass Whop auth (testing) |
| DEV_USER_ID   | No       | User ID used when DEV_MODE is true       |
| PORT          | No       | Auto-set by Railway                      |
