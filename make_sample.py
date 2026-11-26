"""Create a leads.xlsx with the right columns, generic call scripts and 3 fictional test rows.

The test numbers are in reserved fictional ranges (US 555-01xx, UK Ofcom 07700 900xxx)
or all zeros (PK). Replace one with your own mobile to try a real call.
"""
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "leads.xlsx")

COLUMNS = [
    "ID", "Business Name", "Category", "Has Chat Widget", "WhatsApp/IG Active", "Fit Score",
    "Owner/Decision Maker", "Phone", "Alt Phone", "Email", "Website", "Address", "City", "Country", "Timezone",
    "Google Rating/Reviews", "Business Notes", "Personalized Opener", "Script", "DNC Checked",
    "Status", "Call Outcome", "Attempts", "Call Duration", "Notes", "Follow-up Date", "Calendly Link Sent", "Source",
]

TEST_ROWS = [
    ["T1", "TEST - Sunrise Dental", "Dental clinic", "No", "IG only", 5, "Dr. Sarah Test", "+1 555 010 0001", "",
     "", "example.com", "1 Test St", "Austin", "US", "America/Chicago", "4.6 (120)",
     "3 reviews mention calls going to voicemail", "Saw a few reviews saying it's hard to reach the front desk after 5pm.",
     "", "N/A (business line)", "", "", "", "", "", "", "", "test row"],
    ["T2", "TEST - Northside Lettings", "Real estate agency", "Basic form only", "WhatsApp yes", 4, "James Test",
     "07700 900001", "", "", "example.co.uk", "2 Test Rd", "Manchester", "UK", "", "4.2 (58)",
     "Posts listings on Instagram daily, no instant reply on site", "You post new listings daily - who answers the DMs at 10pm?",
     "", "TPS/CTPS: check", "", "", "", "", "", "", "", "test row"],
    ["T3", "TEST - Lahore Smile Clinic", "Dental clinic", "No", "WhatsApp yes", 5, "Dr. Ali Test",
     "0300 0000000", "", "", "example.pk", "3 Test Blvd, DHA", "Lahore", "PK", "", "4.8 (210)",
     "Takes bookings only via WhatsApp manually", "Aap ki clinic WhatsApp pe bookings leti hai - raat ko messages kaun handle karta hai?",
     "", "N/A", "", "", "", "", "", "", "", "test row"],
]

SCRIPTS = [
    ("US", """Hi, is this {owner}? Hey {owner}, this is {my_name} from {product} - I'll be quick, is now a bad time?

[If OK]
{opener}

The reason I'm calling: a lot of {category}s we talk to miss enquiries that come in after hours or while the team is busy.

{product} helps {business} answer those enquiries and book them straight into your calendar.

Quick question - roughly how many enquiries do you get outside business hours?

[Listen, then]
Would it be worth 30 minutes this week for a short demo? I can send you a link to pick a time - what's the best email for that?

[Close] Perfect - sending it now. Thanks {owner}!"""),
    ("UK", """Hi, is that {owner}? Hi {owner}, it's {my_name} from {product} - have you got 30 seconds? I'll keep it brief.

{opener}

We work with {category}s to make sure no enquiry goes unanswered, evenings and weekends included.

Out of curiosity - who's handling enquiries that come in after hours at the moment?

[Listen, then]
Would you be open to a quick 30-minute demo for {business}? I'll send over a booking link - what's the best email for you?

[Close] Brilliant, I'll send that across now. Cheers {owner}."""),
    ("PK", """Assalam o Alaikum, kya meri baat {owner} se ho rahi hai? Main {my_name} bol raha hoon {product} se - sirf 1 minute chahiye.

{opener}

Hum {category} businesses ki madad karte hain taake koi customer enquiry miss na ho - din ho ya raat.

Aap ke paas din mein kitni enquiries aati hain, aur raat ko unhein kaun jawab deta hai?

[Listen, then]
Kya main aap ko 30 minute ka demo dikha doon? Main aap ko booking link bhej deta hoon.

[Close] Bohat shukriya {owner} sahab, link abhi bhej raha hoon."""),
    ("Objections", """"We already have something for that"
-> Makes sense - what happens today when an enquiry comes in at 9pm?

"No time / send me info"
-> Totally understand. The demo is 30 minutes and focused on your business, not slides. What day is lighter for you?

"Too expensive"
-> Fair question - what does a missed booking cost you today?

"Not interested"
-> No problem. Just so I don't bother you again - is it not a priority right now, or already handled?"""),
    ("Voicemail", """Hi {owner}, it's {my_name} from {product}. I had a quick idea for {business} about catching enquiries that come in after hours. I'll send you a short message with a link - or call me back on this number. Thanks!"""),
]


def main():
    if os.path.exists(OUT):
        raise SystemExit("leads.xlsx already exists - not overwriting.")
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(COLUMNS)
    for row in TEST_ROWS:
        ws.append(row)
    head = PatternFill("solid", fgColor="4F46E5")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head
    widths = {"Business Name": 28, "Business Notes": 40, "Personalized Opener": 45, "Script": 30, "Notes": 40,
              "Address": 30, "Website": 24, "Phone": 18, "Call Outcome": 26}
    for i, col in enumerate(COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(col, 15)
    ws.freeze_panes = "C2"
    status_col = ws.cell(row=1, column=COLUMNS.index("Status") + 1).column_letter
    dv = DataValidation(type="list", allow_blank=True, formula1='"New,Interested,Demo booked,Callback,No answer,Voicemail,Not interested,Wrong number,Do not call"')
    ws.add_data_validation(dv)
    dv.add("%s2:%s5000" % (status_col, status_col))

    sc = wb.create_sheet("Scripts")
    sc.append(["Name", "Script  (placeholders: {owner} {business} {category} {city} {opener} {my_name} {product} {calendly})"])
    for name, text in SCRIPTS:
        sc.append([name, text])
    sc.column_dimensions["A"].width = 14
    sc.column_dimensions["B"].width = 120
    for row in sc.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    sc["A1"].font = sc["B1"].font = Font(bold=True)
    wb.save(OUT)
    print("Created", OUT)


if __name__ == "__main__":
    main()
