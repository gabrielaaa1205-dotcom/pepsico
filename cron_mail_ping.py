"""Ping the protected mail sync endpoint from a Render cron job."""
import os,urllib.request,json
url=os.getenv('MAIL_SYNC_ENDPOINT','https://pepsico-zo2e.onrender.com/api/mail/refresh')
token=os.environ['MAIL_SYNC_WEBHOOK_TOKEN']
req=urllib.request.Request(url,data=b'{}',method='POST',headers={'Content-Type':'application/json','X-Mail-Sync-Token':token})
with urllib.request.urlopen(req,timeout=55) as response:
    print(json.dumps({'http_status':response.status,'triggered':response.status==202}))
