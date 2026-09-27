## 4. User stories

Made-up data for Toolshed, a tool-lending library run by neighbours. Stories speak for the borrower (P1) unless they name a volunteer (P2) or a coordinator (P3), and the bracketed tag after a title only says when the story ships.

| Journey (§3) | Epics |
| :-- | :-- |
| Joining | E1 |
| Finding a tool | E2 |
| Borrowing | E3, then US-1.5 |
| Returning | E4 |

### E1 Membership

- **US-1.1 Sign up with a phone number [MVP].** I join with my phone number and a one-time code, so that I can borrow without remembering a password.
  - The code expires after ten minutes.
  - A number can hold one membership.
- **US-1.2 Proof of address [MVP; pilot neighbourhood only].** I upload a photo of a utility bill, so that a volunteer (P2) can confirm I live nearby.
  - Volunteers see the photo only until they approve or reject it.
  - The photo is deleted within 24 hours of the decision.
- **US-1.3 Membership card [Lite].** I get a QR code in the app, so that the desk can check me in without a plastic card (US-1.1).
- **US-1.4 Household members [Later].** I add up to three people from my household, so that they can borrow on my membership.
  - Each member has their own borrowing history (US-3.2).
  - The account holder is responsible for late returns.
- **US-1.6 Pause my membership [Not now].** I pause my membership for up to six months, so that I stop getting reminders while I'm away.
- **US-1.7 Single sign-on [MVP]** — merged into US-1.1: one sign-in path is enough for the pilot.

### E2: Catalogue

- **US-2.1 Browse by category [MVP].** I browse tools by category (garden, woodwork, cleaning, kitchen), so that I find what exists without knowing its name.
- **US-2.2 Search [MVP].** I search the catalogue by name or by job (`cut a hedge`), so that I find the right tool fast (US-2.1).
- **US-2.3 Availability calendar [MVP+].** I see, for each tool, the days it is free over the next four weeks, so that I plan my project around it.
  - Reserved days are greyed out.
  - Days the shed is closed (§5) are hidden.
- **US-2.4 Tool page [MVP].**
  - Every tool has a photo, its condition, the deposit and a short safety note.
  - Power tools also list the protective gear to borrow with them (US-2.5).
- **US-2.5 Safety kit suggestions [Lite: volunteers curate the list].** When I reserve a power tool, I'm offered the matching gloves, goggles or ear defenders, so that I don't start a job unprotected (G1).
- **US-2.6 Deposit table [MVP].** I see what each kind of tool costs to borrow, so that there are no surprises at the desk.

| Kind | Deposit | Loan length |
| :-- | --: | :-- |
| Hand tool | €5 | 7 days |
| Power tool | €20 | 3 days |

### E3 — Borrowing

- **US-3.1 Reserve a tool [MVP].** I reserve a free tool for a pickup day, so that it is waiting for me at the desk (US-2.3).
  - A reservation lasts one loan period (US-2.6).
  - I can hold at most **two** reservations at a time.
    - Volunteers can lift the limit for community projects.
    - The limit counts power tools twice.
- **US-3.2 Borrowing history [Lite].** I see every tool I borrowed and when I returned it, so that I can find the one that worked well last time.
- **US-3.3 Pickup reminder [MVP].** I get a text message the morning of my pickup day, so that I don't forget to come (US-1.1, 3.1).
- **US-1.5 First-loan walkthrough [MVP].** On my first pickup a volunteer shows me how the loan works, so that I know the rules before I take anything home.
  - The walkthrough is logged on my membership (US-1.1).
- **US-3.5 Group loans [decision].** A community group borrows several tools under one reservation, so that a clean-up day needs one booking, not ten.

---

### E4 Returns and repairs

- **US-4.1 Return at the desk [MVP].** A volunteer scans the tool's code when I bring it back, so that my loan closes and my deposit is refunded.
- **US-4.2 Report a problem [MVP].** I report a broken or blunt tool when I return it, so that the next borrower gets a working one.
  - The tool is marked _in repair_ until a volunteer checks it (US-4.3).
- **US-4.3 Repair queue [Lite; volunteers only].** A volunteer (P2) sees the tools waiting for repair, oldest first, so that nothing sits broken for weeks.
- **US-4.4 Late return reminders [MVP].** I get a reminder the day before a loan ends and each day after, so that tools come back on time.
- **US-4.5 Seasonal stock-take [Later].**
- **US-4.6 Automatic fines [MVP]** — removed: fines put off the people the shed most wants to reach.
