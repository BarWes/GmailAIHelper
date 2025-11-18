# test_single_email.py
import os
import pickle
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If modifying these scopes, delete the file token.pickle
# For this project I need onlt read permissions from gmail
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

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

# Get list of messages (just 1)
print("\nStep 2: Getting most recent email ID")
results = service.users().messages().list(userId='me', maxResults=1).execute()
messages = results.get('messages', [])

if not messages:
    print("No emails found!")

else:
    # Get the first message ID
    msg_id = messages[0]['id']
    print(f"Found email with ID: {msg_id}")
    
    # Get the full message details
    print("\nStep 3: Fetching email details...")
    message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
    print("Email fetched!")
    
    # Extract headers
    print("\nStep 4: Extracting email information...")
    headers = message['payload']['headers']
    
    subject = None
    sender = None
    date = None
    
    for header in headers:
        if header['name'] == 'Subject':
            subject = header['value']
        elif header['name'] == 'From':
            sender = header['value']
        elif header['name'] == 'Date':
            date = header['value']
    
    # Get snippet (preview text)
    snippet = message.get('snippet', '')
    
    # Print everything nicely
    print("MOST RECENT EMAIL:")
    print(f"From: {sender}")
    print(f"Date: {date}")
    print(f"Subject: {subject}")
    print(f"\nPreview: {snippet[:200]}...")