# trySearch Replica with Backend

This repository contains a static frontend replica of the trySearch homepage plus a simple Flask backend.

## Local setup

1. Install dependencies:

```bash
cd /Users/ariyannath/Desktop/trySearch
python3 -m pip install -r requirements.txt
```

2. Start the app (SQLite for local development):

```bash
python3 server.py
```

3. Open `http://127.0.0.1:8000` in your browser.

## Backend Options

The app supports three database backends:

- **server.py** — uses local SQLite (best for local dev)
- **server_pg.py** — uses Postgres (good for production with DATABASE_URL)
- **server_mongo.py** — uses MongoDB Atlas (requires MONGODB_URI) ← **recommended for scalability**

### API Endpoints

- `GET /api/contacts` — fetch saved contact submissions
- `POST /api/contacts` — save a contact submission
- `GET /api/health` — check database connection status
- `POST /api/register` — create a user account
- `POST /api/login` — log in with username and password
- `POST /api/logout` — log out
- `GET /api/me` — get current login status
- `GET /admin/contacts` — view all contacts (requires login)

## Deployment with MongoDB (Recommended)

### Step 1: Create MongoDB Atlas Cluster

1. Go to https://www.mongodb.com/cloud/atlas
2. Sign up (free tier available)
3. Create a new project
4. Click **Build a Cluster** → choose **Free tier (M0)**
5. Select your region
6. Click **Create Cluster**
7. Once created, click **Connect** → **Drivers** → copy the connection string
   - The string looks like: `mongodb+srv://user:password@cluster.mongodb.net/dbname?retryWrites=true&w=majority`

### Step 2: Set MongoDB User & Network Access

1. In MongoDB Atlas, go to **Database Access** → **Add New Database User**
   - Choose **Password** authentication
   - Save the username and password
   - Make sure to copy the connection string format with your credentials

2. Go to **Network Access** → **Add IP Address**
   - Choose **Allow Access from Anywhere** (0.0.0.0/0) for development
   - Or add your specific IP for better security

### Step 3: Deploy to Render

1. Push this repository to GitHub (if not already pushed)
2. Go to https://render.com
3. Create a new web service and connect your GitHub repo
4. Set the following environment variables in **Environment**:

   | Variable | Value |
   |----------|-------|
   | MONGODB_URI | `mongodb+srv://user:password@cluster.mongodb.net/trysearch?retryWrites=true&w=majority` |
   | SECRET_KEY | A strong random string (e.g., from https://randomkeygen.com/) |

5. Make sure **Procfile** exists (it should run `gunicorn server_mongo:app`)
6. Click **Deploy**

### Step 4: Verify

After deployment:

1. Open your site: `https://your-service.onrender.com`
2. Check health: `https://your-service.onrender.com/api/health`
   - Should show: `{"status":"ok","db":"mongodb"}`
3. Submit a contact form
4. Check `/api/contacts` to see your data saved
5. Redeploy again — your data should persist ✓

### Step 5: Migrate from Postgres (optional)

If you have existing data in Postgres and want to copy it to MongoDB:

```bash
export MONGODB_URI="mongodb+srv://user:password@cluster.mongodb.net/trysearch?retryWrites=true&w=majority"
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
python3 migrate_postgres_to_mongo.py
```

Then redeploy your app on Render with MONGODB_URI set.

## Authentication

The site includes username/email and password authentication backed by the database.

- **Register**: `/register`
- **Login**: `/login`
- Sessions use signed cookies (Flask). When "Remember me" is checked, the session lasts 30 days.
- Set `SECRET_KEY` environment variable to a strong random string for secure cookies.

## Files

- `server.py` — Flask app with local SQLite
- `server_pg.py` — Flask app with Postgres (requires DATABASE_URL)
- `server_mongo.py` — Flask app with MongoDB (requires MONGODB_URI) ← **Use this for Render + MongoDB**
- `migrate_sqlite_to_postgres.py` — Migration script from SQLite → Postgres
- `migrate_postgres_to_mongo.py` — Migration script from Postgres → MongoDB
- `Procfile` — Tells Render which server to run
- `requirements.txt` — Python dependencies (Flask, gunicorn, pymongo, SQLAlchemy, psycopg)

## Why MongoDB?

- **Scalability**: Easy to scale horizontally
- **Flexibility**: Schema-less documents (no migrations needed)
- **Reliability**: MongoDB Atlas is fully managed and backed up
- **Free tier**: Generous free tier on MongoDB Atlas
- **Easy integration**: Works seamlessly with the current Flask app

## Troubleshooting

**MongoDB connection error**: Ensure MONGODB_URI is set correctly and your IP is whitelisted in MongoDB Atlas Network Access.

**Data not persisting**: Make sure you're running `server_mongo.py` (check Procfile) and MONGODB_URI is set as an environment variable.

**Duplicate key error on register**: Clear the users collection in MongoDB and try again (or use a different username).

## Support

For issues or questions, check:
- MongoDB Atlas docs: https://docs.atlas.mongodb.com/
- Render docs: https://render.com/docs
- Flask docs: https://flask.palletsprojects.com/
