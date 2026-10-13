"""Gmail over IMAP with an app password. (The Google sign-in version is parked in later/gmail_oauth.py until
Google's app verification is done: the same connect link will then switch to it.)"""
from .imap import Imap

GMAIL_HELP = ("Google did not accept this email address and app password. Check that you pasted the 16-character app "
              "password (not your usual Google password) and that the email address is right.")


class Gmail(Imap):
    id = "gmail"
    label = "Gmail"
    interval = 60
    method = "app_password"
    default_host = "imap.gmail.com"
    help = GMAIL_HELP
