import os
import re
import secrets
import string
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="Feedback Sentiment System API")

# --- CORS SETTINGS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- SUPABASE SETUP ---
# Note: In a production app, move these to your .env file
URL = "https://edrcecxnbvqzoihmqzac.supabase.co"
KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkcmNlY3huYnZxem9paG1xemFjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ5MjY5NzEsImV4cCI6MjA5MDUwMjk3MX0.k0tF2k9j8Y8f7H74EOjiLncvsx6MZ0PA4_MWMWKdV8A"
supabase: Client = create_client(URL, KEY)

# --- HELPER: RANDOM CODE GENERATOR ---
def generate_random_org_code(org_name: str):
    """Generates a secure, random code like AB-X9Z2-2026"""
    prefix = re.sub(r'\W+', '', org_name)[:2].upper()
    if not prefix: prefix = "OR"
    alphabet = string.ascii_uppercase + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(4))
    return f"{prefix}-{random_part}-2026"

# --- VALIDATION SCHEMAS ---
class LoginData(BaseModel):
    email: EmailStr
    password: str

# --- ENDPOINTS ---

@app.get("/")
async def root():
    return {"message": "API is running. Visit /docs for documentation."}

# 1. REGISTER: This saves the Email + Password into Supabase Auth
@app.post("/api/register/admin")
async def register_admin(data: LoginData):
    try:
        # sign_up is the "Real App" way—it secures the password immediately
        res = supabase.auth.sign_up({
            "email": data.email.lower(),
            "password": data.password,
        })
        return {"status": "success", "message": "Verification code sent to email"}
    except Exception as e:
        print(f"Registration Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/register/user")
async def register_user(data: LoginData):
    return await register_admin(data)

# 2. VERIFY OTP: Confirms the account and creates the Database Profile
@app.post("/api/verify-otp")
async def verify_otp(data: dict): 
    try:
        email = data.get('email', '').lower()
        token = data.get('token', '')
        role = data.get('role', 'user')

        # Verify the 6-digit code with Supabase
        auth_res = supabase.auth.verify_otp({
            "email": email,
            "token": token,
            "type": "signup" # Crucial: Matches the sign_up flow
        })
        
        if not auth_res.user:
            raise Exception("Invalid or expired OTP")

        uid = auth_res.user.id

        # IF ADMIN: Create Organization and Profile
        if role == 'admin':
            org_name = data.get('org_name', 'My Organization')
            gen_code = generate_random_org_code(org_name)
            
            # Create Org row
            supabase.table("organizations").insert({
                "org_name": org_name,
                "org_code": gen_code,
                "admin_id": uid
            }).execute()

            # Create Profile row
            supabase.table("profiles").insert({
                "id": uid,
                "full_name": data.get('full_name'),
                "role": "admin",
                "org_code": gen_code
            }).execute()
            
            return {
                "status": "success", 
                "org_code": gen_code, 
                "user": {"id": uid, "org_code": gen_code, "full_name": data.get('full_name')}
            }
        
        # IF USER: Join existing org
        else:
            supabase.table("profiles").insert({
                "id": uid,
                "full_name": data.get('full_name'),
                "role": "user",
                "org_code": data.get('org_code')
            }).execute()
            
            return {"status": "success"}

    except Exception as e:
        print(f"Verification Error: {e}")
        raise HTTPException(status_code=400, detail=f"Verification failed: {str(e)}")

# 3. LOGIN: Authenticates and fetches the Profile
@app.post("/api/login")
async def login(data: LoginData):
    try:
        # Authenticate against the encrypted password in auth.users
        auth_res = supabase.auth.sign_in_with_password({
            "email": data.email.lower(), 
            "password": data.password
        })
        
        uid = auth_res.user.id
        
        # Fetch the Profile (Role and Org Code)
        profile = supabase.table("profiles").select("*").eq("id", uid).single().execute()

        return {
            "status": "success",
            "session": auth_res.session,
            "user": profile.data
        }
    except Exception as e:
        print(f"DEBUG LOGIN ERROR: {e}") 
        raise HTTPException(
            status_code=401, 
            detail="Invalid email or password. Ensure your account is verified."
        )

# --- FEEDBACK SYSTEM SECTION ---

class FeedbackData(BaseModel):
    user_id: str
    org_code: str
    category: str
    department: str
    supervisor: str
    feedback_text: str
    is_anonymous: bool

@app.post("/api/submit-feedback")
async def submit_feedback(data: FeedbackData):
    try:
        text = data.feedback_text.lower()
        positive_words = ['good', 'great', 'excellent', 'helpful', 'happy', 'love', 'perfect', 'thanks']
        negative_words = ['bad', 'poor', 'terrible', 'issue', 'problem', 'hate', 'slow', 'difficult']
        
        if any(word in text for word in negative_words):
            sentiment = "Negative"
        elif any(word in text for word in positive_words):
            sentiment = "Positive"
        else:
            sentiment = "Neutral"

        supabase.table("feedbacks").insert({
            "user_id": data.user_id if not data.is_anonymous else None,
            "org_code": data.org_code,
            "category": data.category,
            "department": data.department,
            "supervisor": data.supervisor,
            "content": data.feedback_text,
            "sentiment": sentiment,
            "is_anonymous": data.is_anonymous
        }).execute()

        return {"status": "success", "sentiment": sentiment}
    except Exception as e:
        print(f"Feedback Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/test-connection")
async def test():
    try:
        supabase.table("profiles").select("count", count="exact").limit(1).execute()
        return {"status": "connected", "database": "accessible"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}