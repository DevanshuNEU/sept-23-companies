// Synthetic research data for the import tests. Every company here is made up.
window.REFERENCE = { floor: 15.29, oldRandom: 29.6, levelTwo: 30.58 };

window.COMPANIES = [
  // ---------- PROVEN CHANNEL
  { id: "kestrel", name: "Kestrel Robotics", cat: "AI / Robotics", hq: "Boston, MA", seed: "Interviewing",
    sizeClass: "small", sizeWhy: "About 60 people, per their team page",
    reach: 71, odds: 33.5, trend: "rising", wage: 3, head: "~60", funding: "Series A",
    newGrad: true, model: false, visa: "Sponsors H-1B", blockers: [],
    reqs: [{ t: "Software Engineer, New Grad", u: "https://jobs.example.com/kestrel/1", d: "2026-10-01" }],
    outreach: { channel: "email", why: "Founder replied to a past note", domain: "kestrel.example" },
    note: "Fixture company with a seed stage and an outreach domain." },
  { id: "lumen", name: "Lumen Freight", cat: "Logistics software", hq: "Chicago, IL", seed: undefined,
    sizeClass: "small", sizeWhy: "About 80 people",
    reach: 40, odds: 20, trend: "flat", wage: 2, head: "~80", funding: "Seed",
    newGrad: false, model: false, visa: "No sponsorship", visaDenied: true,
    blockers: ["Transfers only. They say they will not file a new H-1B petition."],
    reqs: [], outreach: { channel: "linkedin", why: "Engineer posts often" },
    note: "Fixture company that says no to sponsorship." },
  { id: "orbital", name: "Orbital Foods", cat: "Food tech", hq: "Austin, TX",
    sizeClass: "cleared", sizeWhy: "Cleared defence contractor, per the old dashboard",
    reach: 22, odds: 10, trend: "flat", wage: 2, head: "~900", funding: "Private",
    newGrad: false, model: false, visa: "Unclear", blockers: [], reqs: [],
    note: "Fixture company in the cleared size class." },
  { id: "quarry", name: "Quarry Labs", cat: "Developer tools", hq: "Denver, CO",
    sizeClass: "big", sizeWhy: "well over 500 people",
    reach: 55, odds: 28, trend: "rising", wage: 3, head: "~900", funding: "Series C",
    newGrad: true, model: false, visa: "Sponsors H-1B", blockers: [],
    reqs: [{ t: "Backend Engineer", u: "https://jobs.example.com/quarry/7", d: "2026-09-28" }],
    note: "Fixture company that is well over the size cap." },
  { id: "tidewater", name: "Tidewater AI", cat: "AI / Data", hq: "Seattle, WA",
    sizeClass: "small", sizeWhy: "About 45 people",
    reach: 64, odds: 30, trend: "rising", wage: 3, head: "~45", funding: "Series A",
    newGrad: true, model: false, visa: "Sponsors H-1B", blockers: [],
    reqs: [{ t: "Machine Learning Engineer", u: "https://jobs.example.com/tidewater/3", d: "2026-10-05" }],
    note: "Plain fixture company." },
];

window.RULED_OUT = [
  { n: "Saltmarsh Bio", why: "Eleven certified LCAs but no new-employment approvals in three years." },
  { n: "Ferrous Grid / Tidewater AI", why: "Both are ruled out by one note in the old list." },
];

window.META = { refreshedAt: "2026-10-15T20:25" };
