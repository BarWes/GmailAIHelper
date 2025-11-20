import os
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import base64
import re
from llama_cpp import Llama
import sys
import json
import redis
import redis
import hashlib

# define number of emails to go through
NUMBER_OF_EMAILS = 50

REDIS_HOST = "localhost"
REDIS_PORT = 6379
CACHE_EXPIRY = 28800  # 8 hours

# Initialize Redis
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()
    print("Connected to Redis")
except:
    print("Redis not available")
    redis_client = None

# Generate cache key based on sender, subject, and first 100 chars of body.
def get_cache_key(email_data):
    key_string = f"{email_data['sender']}:{email_data['subject']}:{email_data['body'][:100]}"
    return hashlib.md5(key_string.encode()).hexdigest()

# Add model path from env variables
with open('.env', 'r') as f:
    for line in f:
        if line.startswith('MODEL_PATH'):
            MODEL_PATH = line.strip().split('=')[1]
            os.environ['MODEL_PATH'] = MODEL_PATH

class SuppressOutput:
    def __enter__(self):
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.close()
        sys.stderr.close()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

# Load model silently
print("Loading model")
with SuppressOutput():
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=2048,
        n_threads=4,
        verbose=False
    )
print("Model loaded!")

# Analyze the email using the local LLM
def analyzeEmailWithLLM(email_data):

    # Start by checking the cache
    cache_key = get_cache_key(email_data)
    if redis_client:
        cached = redis_client.get(f"analysis:{cache_key}")
        if cached:
            print("Email grabbed from cache")
            return json.loads(cached)

    prompt = f"""You MUST output ONLY valid JSON.

Analyze the following email and fill in this JSON object:

{{
  "category": "",
  "priority": "",
  "needs_response": ""
}}

Use only the following allowed values:

category = Work, School, Shopping, Social, Finance, Newsletter, Spam, Personal, Other
priority = Urgent, Important, Normal, Low
needs_response = Yes, No, Maybe

Email details:
From: {email_data['sender']}
Subject: {email_data['subject']}
Body: {email_data['body'][:300]}

Now return ONLY the JSON object:
"""

    print("Calling LLM...")
    with SuppressOutput():
        raw = llm(
            prompt,
            max_tokens=200,
            temperature=0.1,
            echo=False
        )
        

    if not raw or "choices" not in raw:
        analysis = {
            "category": "Other",
            "priority": "Normal",
            "needs_response": "Maybe"
        }
    
        # if redis is available cache the analysis for use later
        if redis_client:
            redis_client.setex(f"analysis:{cache_key}", CACHE_EXPIRY, json.dumps(analysis))

        return analysis

    llm_response = raw["choices"][0]["text"].strip()

    # Auto close the JSON if llama cuts early
    # Add a missing "}" if the model forgot it
    if llm_response.count("{") > llm_response.count("}"):
        llm_response = llm_response + "}"

    # Add missing field if model returns only 2 of them
    for field, default in {
        "category": "Other",
        "priority": "Normal",
        "needs_response": "Maybe"
    }.items():
        if f"\"{field}\"" not in llm_response:
            
            # Safely append
            llm_response = llm_response.rstrip("}")
            llm_response += f', "{field}": "{default}"' + "}"

    # Parse JSON
    try:
        
        # Extract ALL JSON objects
        json_matches = re.findall(r'\{.*?\}', llm_response, re.DOTALL)

        # Nothing found → fallback
        if not json_matches:
            raise json.JSONDecodeError("no json", llm_response, 0)

        # Try each JSON object, from LAST to FIRST
        for candidate in reversed(json_matches):
            try:
                analysis = json.loads(candidate)

                # Save result to Redis
                if redis_client:
                    redis_client.setex(
                        f"analysis:{cache_key}",
                        CACHE_EXPIRY,
                        json.dumps(analysis)
                    )

                return analysis
            except json.JSONDecodeError:
                continue

        # If all failed → fallback
        raise json.JSONDecodeError("all invalid", llm_response, 0)

    except Exception:
        print(f"  ⚠ Failed to parse: {llm_response[:120]}")
        return {
            "category": "Other",
            "priority": "Normal",
            "needs_response": "Maybe"
        }
    
# Helper function to cache emails
def cacheEmail(email_id, email_data):
    if redis_client:
        redis_client.setex(
            f"email:{email_id}",
            CACHE_EXPIRY,   # same expiry (8 hours)
            json.dumps(email_data)
        )

# Helper function see if an email is already cached
def getCahcedEmail(email_id):
    if redis_client:
        data = redis_client.get(f"email:{email_id}")
        if data:
            return json.loads(data)
    return None

# If modifying these scopes, delete the file token.pickle
# For this project I need onlt read permissions from gmail
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Extract email body from payload
def getEmailBody(payload):

    body = ""
    
    # Check if email has multiple parts (multipart email)
    if 'parts' in payload:
        for part in payload['parts']:

            # Try to get plain text first
            if part['mimeType'] == 'text/plain':
                if 'data' in part['body']:
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    break

            # If no plain text, get HTML and strip tags manually
            elif part['mimeType'] == 'text/html' and not body:
                if 'data' in part['body']:
                    html = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')

                    # Simple regex to remove HTML tags
                    body = re.sub(r'<[^>]+>', '', html)
    else:
        
        # Single part email
        if 'data' in payload['body']:
            data = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
            
            # If it looks like HTML, strip tags
            if '<html' in data.lower() or '<body' in data.lower():
                body = re.sub(r'<[^>]+>', '', data)
            else:
                body = data
    
    # Clean whitespace and truncate
    body = re.sub(r'\s+', ' ', body).strip()
    return body[:500]

def getGmailService():
    credentials = None
    
    # The file token.pickle stores the user's access and refresh tokens
    # It may exist from a previous authentication
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            credentials = pickle.load(token)
    
    # If there are no valid credentials available, let the user log in
    if not credentials or not credentials.valid:

        # If they exist and are expired or need refreshing, we call Refresh()
        # function to get a "fresh" access token
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        
        # First time authentication:
        else:

            # Create OAuth flow from credentials.json
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            
            # This will tart a temporary local web server on a random available port
            # Open Google auth page and then user will log in and grant permission
            # Google sends back auth code upon success
            # This will eventually return the credentials object
            credentials = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)
    
    # build will create the gmail api client, gmail specifies which api to use
    # Return the serivce object
    service = build('gmail', 'v1', credentials=credentials)
    return service

# Connect to Gmail
print("Step 1: Connecting to Gmail")
service = getGmailService()
print("Connected successfully!")

# Get list of messages
print(f"\nStep 2: Getting most recent {NUMBER_OF_EMAILS} emails IDs")
results = service.users().messages().list(userId='me', maxResults=NUMBER_OF_EMAILS).execute()
messages = results.get('messages', [])

# In case you found no emails
if not messages:
    print("No emails found!")
    exit()

# In case you did
else:

    # Getting most recent 50 emails
    # LIST lists them but doesnt actually return the emails themselves
    # We need this for the email IDs
    results = service.users().messages().list(userId='me', maxResults=NUMBER_OF_EMAILS).execute()
    messages = results.get('messages', [])
    
    # Create a list to hold all the emails
    emails = []

    # Run over the data fetched from the gmail api
    for i, msg in enumerate(messages):

        # Get a single message id from the list
        msg_id = msg['id']
        print(f"[{i+1}/{len(messages)}] Fetching email {msg_id}", end='\r')

        # Try redis first
        cached = None
        if redis_client:
            cached_data = redis_client.get(f"email:{msg_id}")
            if cached_data:
                cached = json.loads(cached_data)
                emails.append(cached)
                continue  # skip Gmail API
        
        # Get the full message details through the id we got before
        message = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        
        # Extract headers
        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')

        # Extract body using the function
        body = getEmailBody(message['payload'])
        
        email_data = {
            'id': msg_id,
            'subject': subject,
            'sender': sender,
            'date': date,
            'body': body
        }

        emails.append(email_data)

        # Cache the email you found
        if redis_client:
            redis_client.setex(
                f"email:{msg_id}",
                CACHE_EXPIRY,
                json.dumps(email_data)
            )

    print("\n")
    print(f"Fetched {len(emails)} emails")

    # Print first 5 as test
    for i, email in enumerate(emails[:5], 1):
        print(f"\n{i}. {email['subject']}")
        print(f"   From: {email['sender']}")
        print(f"   Body preview: {email['body'][:100]}...")

print("\nAnalyzing emails...")
for i, email in enumerate(emails, 1):
    print(f"\n[{i}/{len(emails)}] {email['subject'][:50]}")
    analysis = analyzeEmailWithLLM(email)
    email['analysis'] = analysis
    print(f"Category: {analysis['category']}, Priority: {analysis['priority']}, Need response: {analysis['needs_response']}")
