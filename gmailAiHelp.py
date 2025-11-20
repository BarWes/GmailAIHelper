# test_single_email.py
import os
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import base64
import re

# define number of emails to go through
NUMBER_OF_EMAILS = 50

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
        
        # Get the full message details through the id we got before
        message = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        
        # Extract headers
        headers = message['payload']['headers']
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
        date = next((h['value'] for h in headers if h['name'] == 'Date'), '')

        # Extract body using the function
        body = getEmailBody(message['payload'])
        
        emails.append({
            'subject': subject,
            'sender': sender,
            'date': date,
            'body': body
        })


    print("\n")
    print(f"Fetched {len(emails)} emails")

    # Print first 5 as test
    for i, email in enumerate(emails[:5], 1):
        print(f"\n{i}. {email['subject']}")
        print(f"   From: {email['sender']}")
        print(f"   Body preview: {email['body'][:100]}...")

