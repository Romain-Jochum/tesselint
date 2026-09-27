# tesselint proposals · run 20260102-090000 · snapshot 90b096f9… · typesafe/jev-1.13-20260917
Calibration: not run (tesselint eval RUN --make-labels)

Summary: 24 stories · 10 stories to merge away (1 alternative container folded, 2 borderline) · 2 overlaps (1 borderline) · 2 splits (2 borderline) · 1 leave alone

Top merges (10 of 10):
- [M1](#M1) US-6.3 "Sign up for a work party" → US-6.2 "Suggest a work party job and Report rats at the compost" · m 0.93
- [M2](#M2) US-4.2 "Post surplus produce" → US-6.2 "Suggest a work party job and Report rats at the compost" · m 0.92
- [M3](#M3) US-5.1 "Book a green waste skip slot" → US-6.2 "Suggest a work party job and Report rats at the compost" · m 0.90
- [M4](#M4) US-3.1 "See my plot's water use" → US-4.1 "Water use with a seasonal allowance" · m 0.89
- [M5](#M5) US-8.1 "Settle the allotment fee online" → US-7.1 "Pay annual plot rent" · m 0.81
- [M6](#M6) US-6.1 "Track my volunteer hours" → US-7.3 "Volunteer hours shared across a joint tenancy" · m 0.79
- [M7](#M7) US-8.3 "Request the reduced fee" → US-7.2 "Apply for a concession rate" · m 0.75
- [M9](#M9) US-7.6 "Upload concession proof" → US-7.2 "Apply for a concession rate" · m 0.65
- [M10](#M10) US-8.2 "Record a plot inspection" → US-1.1 "Rate allotments on a grounds round" · m 0.59 · borderline
- [M11](#M11) US-7.5 "Renew by direct debit" → US-8.99 "Pay a refundable plot deposit" · m 0.56 · borderline

Top splits (2 of 2):
- [S1](#S1) US-6.2 "Suggest a work party job and Report rats at the compost" · bundled 0.87 · borderline
- [S2](#S2) US-3.3 "Join a watering rota and Unpaid rent reminders" · bundled 0.83 · borderline

How to read this: o is Jev's probability that two stories overlap at all (duplicate, containment or partial overlap), and m that one can absorb the other (o minus partial overlap). A merge needs m ≥ 0.50; an overlap is a cluster with mean o ≥ 0.70 that no single merge covers (merge the stories or move the shared part); a split needs bundled ≥ 0.80, or ≥ 0.70 when the story's o ≥ 0.70 edges reach two other clusters, where bundled is atomic.v2's probability that the story asks for two or more separate capabilities. Borderline means the deciding value is within 0.10 of its threshold, inside Jev's run-to-run drift: read the stories before acting. In the fwd/swap line, A is the story that comes first in the document, fwd and swap are the two orders Jev was asked in (top two answers each) and cos is the local embedding similarity. ⊃ marks a containment, = a duplicate and ~ any other overlap; also related lists other stories at o ≥ 0.40. Merge shows each story to merge away once, under its strongest container, and names its weaker containers, with their ids, in also contained by; Hubs lists the stories that contain 3 or more others. Sections run strongest first; ⚠ marks proposals that contradict each other.

## Hubs

A hub usually means the hub itself bundles capabilities (see its split proposal, if any) or the stories it contains restate parts of it.

- US-6.2 "Suggest a work party job and Report rats at the compost" contains 3: US-6.3 (m 0.93), US-4.2 (m 0.92), US-5.1 (m 0.90) · split [S1](#S1) (bundled 0.87)

## Merge

<a id="M1"></a>
### M1 · merge US-6.3 → US-6.2 (containment) · m 0.93
- keep US-6.2 "Suggest a work party job and Report rats at the compost" (bundled 0.87) · drop US-6.3 "Sign up for a work party" (bundled 0.43)
- fwd A⊃B 0.96 / partial 0.03 · swap A⊃B 0.89 / same-area 0.06 · o 0.96 · keep in C1 · cos 0.37
- also related: US-6.2 ⊃ US-4.2 (o 0.98) · US-6.2 ⊃ US-5.1 (o 0.94) · US-3.2 ~ US-6.2 (o 0.56) · US-6.2 ~ US-8.3 (o 0.52) · US-1.2 ~ US-6.3 (o 0.48)
- ⚠ conflicts with S1

<a id="M2"></a>
### M2 · merge US-4.2 → US-6.2 (containment) · m 0.92
- keep US-6.2 "Suggest a work party job and Report rats at the compost" (bundled 0.87) · drop US-4.2 "Post surplus produce" (bundled 0.52)
- fwd B⊃A 0.91 / partial 0.09 · swap B⊃A 0.93 / partial 0.04 · o 0.98 · C1 · cos 0.13
- also related: US-6.2 ⊃ US-6.3 (o 0.96) · US-6.2 ⊃ US-5.1 (o 0.94) · US-3.2 ~ US-6.2 (o 0.56) · US-6.2 ~ US-8.3 (o 0.52) · US-4.2 ~ US-6.1 (o 0.41)
- ⚠ conflicts with S1

<a id="M3"></a>
### M3 · merge US-5.1 → US-6.2 (containment) · m 0.90
- keep US-6.2 "Suggest a work party job and Report rats at the compost" (bundled 0.87) · drop US-5.1 "Book a green waste skip slot" (bundled 0.22)
- fwd B⊃A 0.93 / same-area 0.04 · swap B⊃A 0.87 / same-area 0.09 · o 0.94 · keep in C1 · cos 0.18
- also related: US-6.2 ⊃ US-4.2 (o 0.98) · US-6.2 ⊃ US-6.3 (o 0.96) · US-3.2 ~ US-6.2 (o 0.56) · US-6.2 ~ US-8.3 (o 0.52)
- ⚠ conflicts with S1

<a id="M4"></a>
### M4 · merge US-3.1 → US-4.1 (containment) · m 0.89
- keep US-4.1 "Water use with a seasonal allowance" (bundled 0.55) · drop US-3.1 "See my plot's water use" (bundled 0.27)
- fwd B⊃A 0.93 / same-area 0.05 · swap B⊃A 0.85 / partial 0.14 · o 0.97 · C2 · cos 0.83
- also contained by: US-3.4 (m 0.74, M8)
- also related: US-3.4 ⊃ US-3.1 (o 0.94) · US-4.1 ~ US-6.1 (o 0.56)

<a id="M5"></a>
### M5 · merge US-8.1 → US-7.1 (duplicate) · m 0.81
- keep US-7.1 "Pay annual plot rent" (bundled 0.12) · drop US-8.1 "Settle the allotment fee online" (bundled 0.27)
- fwd dup 0.79 / partial 0.18 · swap dup 0.82 / partial 0.12 · o 0.95 · C3 · cos 0.31
- also related: US-6.1 ~ US-8.1 (o 0.48) · US-2.2 ~ US-8.1 (o 0.47) · US-1.2 ~ US-7.1 (o 0.41)

<a id="M6"></a>
### M6 · merge US-6.1 → US-7.3 (containment) · m 0.79
- keep US-7.3 "Volunteer hours shared across a joint tenancy" (bundled 0.12) · drop US-6.1 "Track my volunteer hours" (bundled 0.44)
- fwd B⊃A 0.84 / same-area 0.15 · swap B⊃A 0.74 / same-area 0.14 · o 0.85 · C6 · cos 0.86
- also related: US-4.1 ~ US-6.1 (o 0.56) · US-6.1 ~ US-8.1 (o 0.48) · US-4.2 ~ US-6.1 (o 0.41)

<a id="M7"></a>
### M7 · merge US-8.3 → US-7.2 (duplicate) · m 0.75
- keep US-7.2 "Apply for a concession rate" (bundled 0.05) · drop US-8.3 "Request the reduced fee" (bundled 0.01)
- fwd dup 0.79 / same-area 0.18 · swap dup 0.71 / partial 0.20 · o 0.86 · C5 · cos 0.52
- also related: US-7.2 ⊃ US-7.6 (o 0.76) · US-6.2 ~ US-8.3 (o 0.52)

<a id="M9"></a>
### M9 · merge US-7.6 → US-7.2 (containment) · m 0.65
- keep US-7.2 "Apply for a concession rate" (bundled 0.05) · drop US-7.6 "Upload concession proof" (bundled 0.01)
- fwd A⊃B 0.55 / same-area 0.25 · swap A⊃B 0.75 / same-area 0.23 · o 0.76 · keep in C5 · cos 0.50
- also related: US-7.2 = US-8.3 (o 0.86) · US-1.1 ~ US-7.6 (o 0.47)

<a id="M10"></a>
### M10 · merge US-8.2 → US-1.1 (duplicate) · m 0.59 · borderline
- keep US-1.1 "Rate allotments on a grounds round" (bundled 0.40) · drop US-8.2 "Record a plot inspection" (bundled 0.09)
- fwd dup 0.60 / same-area 0.24 · swap dup 0.58 / partial 0.28 · o 0.81 · C8 · cos 0.33
- also related: US-1.1 ~ US-7.6 (o 0.47)

<a id="M11"></a>
### M11 · merge US-7.5 → US-8.99 (duplicate) · m 0.56 · borderline
- keep US-8.99 "Pay a refundable plot deposit" (bundled 0.11) · drop US-7.5 "Renew by direct debit" (bundled 0.28)
- fwd dup 0.56 / partial 0.38 · swap dup 0.56 / partial 0.34 · o 0.92 · C4 · cos 0.34

## Overlap

### O1 · overlap C7: US-1.3, US-2.2 · mean o 0.83
- US-1.3 "Split a full plot into halves" · US-2.2 "Check a tool back in"
- edges: US-1.3 ~ US-2.2 (o 0.83)
- also related: US-2.2 ~ US-8.1 (o 0.47)

### O2 · overlap C9: US-1.2, US-4.3 · mean o 0.79 · borderline
- US-1.2 "Accept or decline a plot offer" · US-4.3 "Borrow from the seed library"
- edges: US-1.2 ~ US-4.3 (o 0.79)
- also related: US-1.2 ~ US-6.3 (o 0.48) · US-1.2 ~ US-7.1 (o 0.41)

## Split

<a id="S1"></a>
### S1 · split US-6.2 · bundled 0.87 · borderline
- US-6.2 "Suggest a work party job and Report rats at the compost"
- 72 words · 3 sub-bullets · in C1 · tag [Lite]
- ⚠ conflicts with M1, M2, M3

<a id="S2"></a>
### S2 · split US-3.3 · bundled 0.83 · borderline
- US-3.3 "Join a watering rota and Unpaid rent reminders"
- 94 words · 4 sub-bullets · tag [MVP+]

## Leave alone (1)
- E3: US-3.2
