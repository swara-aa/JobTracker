from __future__ import annotations

import re


ROLE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Software Engineering",
        (
            "software engineer",
            "software developer",
            "frontend",
            "front end",
            "backend",
            "back end",
            "full stack",
            "web developer",
            "mobile developer",
            "ios developer",
            "android developer",
            "devops",
            "site reliability",
            "sre",
        ),
    ),
    (
        "Data & Analytics",
        (
            "data analyst",
            "business analyst",
            "analytics",
            "data scientist",
            "bi analyst",
            "business intelligence",
            "quantitative analyst",
            "reporting analyst",
        ),
    ),
    (
        "AI / Machine Learning",
        (
            "machine learning",
            "ml engineer",
            "ai engineer",
            "artificial intelligence",
            "deep learning",
            "nlp",
            "computer vision",
            "data mining",
        ),
    ),
    (
        "Product & Project Management",
        (
            "product manager",
            "project manager",
            "program manager",
            "scrum master",
            "product owner",
            "technical program",
            "delivery manager",
        ),
    ),
    (
        "Design & UX",
        (
            "ux designer",
            "ui designer",
            "product designer",
            "graphic designer",
            "visual designer",
            "interaction designer",
            "user researcher",
            "ux researcher",
        ),
    ),
    (
        "Marketing & Communications",
        (
            "marketing",
            "communications",
            "content strategist",
            "copywriter",
            "seo",
            "social media",
            "brand manager",
            "growth",
        ),
    ),
    (
        "Sales & Customer Success",
        (
            "sales",
            "account executive",
            "business development",
            "customer success",
            "client success",
            "sales development",
            "solutions consultant",
        ),
    ),
    (
        "Finance & Accounting",
        (
            "financial analyst",
            "finance",
            "accountant",
            "accounting",
            "auditor",
            "controller",
            "investment",
            "tax",
        ),
    ),
    (
        "Human Resources & Recruiting",
        (
            "human resources",
            "hr ",
            "recruiter",
            "talent acquisition",
            "people operations",
            "compensation",
            "benefits",
        ),
    ),
    (
        "Operations & Supply Chain",
        (
            "operations",
            "supply chain",
            "logistics",
            "procurement",
            "warehouse",
            "inventory",
            "manufacturing",
            "production",
        ),
    ),
    (
        "Healthcare",
        (
            "nurse",
            "rn ",
            "medical assistant",
            "clinical",
            "pharmacist",
            "therapist",
            "healthcare",
            "patient",
        ),
    ),
    (
        "Education",
        (
            "teacher",
            "instructor",
            "professor",
            "tutor",
            "curriculum",
            "academic",
            "student success",
        ),
    ),
    (
        "Legal & Compliance",
        (
            "legal",
            "attorney",
            "paralegal",
            "compliance",
            "risk analyst",
            "contract manager",
            "privacy",
        ),
    ),
    (
        "Engineering",
        (
            "mechanical engineer",
            "electrical engineer",
            "civil engineer",
            "industrial engineer",
            "manufacturing engineer",
            "quality engineer",
            "systems engineer",
            "process engineer",
        ),
    ),
    (
        "Science & Research",
        (
            "research assistant",
            "research associate",
            "laboratory",
            "lab technician",
            "scientist",
            "biology",
            "chemistry",
            "clinical research",
            "research coordinator",
        ),
    ),
    (
        "Business & Strategy",
        (
            "business analyst",
            "strategy analyst",
            "management consultant",
            "consultant",
            "business operations",
            "chief of staff",
            "market research",
        ),
    ),
    (
        "Media & Creative",
        (
            "journalist",
            "editor",
            "producer",
            "video",
            "photographer",
            "media",
            "creative",
            "public relations",
        ),
    ),
    (
        "Public Policy & Government",
        (
            "policy analyst",
            "public policy",
            "government",
            "legislative",
            "public affairs",
            "program associate",
            "case worker",
        ),
    ),
    (
        "Hospitality & Tourism",
        (
            "hospitality",
            "hotel",
            "restaurant",
            "event coordinator",
            "tourism",
            "guest services",
            "food service",
        ),
    ),
)


MAJOR_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "computer science": ("Software Engineering", "Data & Analytics", "AI / Machine Learning"),
    "software engineering": ("Software Engineering", "AI / Machine Learning"),
    "information systems": ("Software Engineering", "Data & Analytics", "Product & Project Management"),
    "information technology": ("Software Engineering", "Data & Analytics"),
    "data science": ("Data & Analytics", "AI / Machine Learning"),
    "statistics": ("Data & Analytics", "Finance & Accounting"),
    "mathematics": ("Data & Analytics", "Finance & Accounting", "AI / Machine Learning"),
    "business": ("Business & Strategy", "Product & Project Management", "Operations & Supply Chain", "Sales & Customer Success"),
    "business administration": ("Business & Strategy", "Product & Project Management", "Operations & Supply Chain"),
    "management": ("Business & Strategy", "Product & Project Management", "Operations & Supply Chain"),
    "marketing": ("Marketing & Communications", "Sales & Customer Success", "Media & Creative"),
    "communications": ("Marketing & Communications", "Media & Creative"),
    "journalism": ("Media & Creative", "Marketing & Communications"),
    "finance": ("Finance & Accounting", "Business & Strategy", "Data & Analytics"),
    "accounting": ("Finance & Accounting",),
    "economics": ("Finance & Accounting", "Data & Analytics", "Business & Strategy"),
    "human resources": ("Human Resources & Recruiting",),
    "psychology": ("Human Resources & Recruiting", "Healthcare", "Education"),
    "biology": ("Science & Research", "Healthcare"),
    "chemistry": ("Science & Research", "Healthcare"),
    "biochemistry": ("Science & Research", "Healthcare"),
    "health sciences": ("Healthcare", "Science & Research"),
    "nursing": ("Healthcare",),
    "pre med": ("Healthcare", "Science & Research"),
    "education": ("Education",),
    "mechanical engineering": ("Engineering", "Operations & Supply Chain"),
    "electrical engineering": ("Engineering", "Software Engineering"),
    "civil engineering": ("Engineering", "Architecture & Construction"),
    "industrial engineering": ("Engineering", "Operations & Supply Chain", "Data & Analytics"),
    "supply chain": ("Operations & Supply Chain",),
    "logistics": ("Operations & Supply Chain",),
    "design": ("Design & UX", "Media & Creative"),
    "graphic design": ("Design & UX", "Media & Creative"),
    "law": ("Legal & Compliance", "Public Policy & Government"),
    "criminal justice": ("Legal & Compliance", "Public Policy & Government"),
    "political science": ("Public Policy & Government", "Legal & Compliance"),
    "public policy": ("Public Policy & Government",),
    "hospitality": ("Hospitality & Tourism", "Sales & Customer Success"),
}

MAJOR_OPTIONS: tuple[str, ...] = tuple(sorted({major.title() for major in MAJOR_ROLE_ALIASES}))


STATE_NAMES: dict[str, str] = {
    "al": "Alabama",
    "alabama": "Alabama",
    "ak": "Alaska",
    "alaska": "Alaska",
    "az": "Arizona",
    "arizona": "Arizona",
    "ar": "Arkansas",
    "arkansas": "Arkansas",
    "ca": "California",
    "california": "California",
    "co": "Colorado",
    "colorado": "Colorado",
    "ct": "Connecticut",
    "connecticut": "Connecticut",
    "dc": "Washington, DC",
    "washington dc": "Washington, DC",
    "de": "Delaware",
    "delaware": "Delaware",
    "fl": "Florida",
    "florida": "Florida",
    "ga": "Georgia",
    "georgia": "Georgia",
    "hi": "Hawaii",
    "hawaii": "Hawaii",
    "id": "Idaho",
    "idaho": "Idaho",
    "il": "Illinois",
    "illinois": "Illinois",
    "in": "Indiana",
    "indiana": "Indiana",
    "ia": "Iowa",
    "iowa": "Iowa",
    "ks": "Kansas",
    "kansas": "Kansas",
    "ky": "Kentucky",
    "kentucky": "Kentucky",
    "la": "Louisiana",
    "louisiana": "Louisiana",
    "me": "Maine",
    "maine": "Maine",
    "md": "Maryland",
    "maryland": "Maryland",
    "ma": "Massachusetts",
    "massachusetts": "Massachusetts",
    "mi": "Michigan",
    "michigan": "Michigan",
    "mn": "Minnesota",
    "minnesota": "Minnesota",
    "ms": "Mississippi",
    "mississippi": "Mississippi",
    "mo": "Missouri",
    "missouri": "Missouri",
    "mt": "Montana",
    "montana": "Montana",
    "ne": "Nebraska",
    "nebraska": "Nebraska",
    "nv": "Nevada",
    "nevada": "Nevada",
    "nh": "New Hampshire",
    "new hampshire": "New Hampshire",
    "nj": "New Jersey",
    "new jersey": "New Jersey",
    "nm": "New Mexico",
    "new mexico": "New Mexico",
    "ny": "New York",
    "new york": "New York",
    "nc": "North Carolina",
    "north carolina": "North Carolina",
    "nd": "North Dakota",
    "north dakota": "North Dakota",
    "oh": "Ohio",
    "ohio": "Ohio",
    "ok": "Oklahoma",
    "oklahoma": "Oklahoma",
    "or": "Oregon",
    "oregon": "Oregon",
    "pa": "Pennsylvania",
    "pennsylvania": "Pennsylvania",
    "ri": "Rhode Island",
    "rhode island": "Rhode Island",
    "sc": "South Carolina",
    "south carolina": "South Carolina",
    "sd": "South Dakota",
    "south dakota": "South Dakota",
    "tn": "Tennessee",
    "tennessee": "Tennessee",
    "tx": "Texas",
    "texas": "Texas",
    "ut": "Utah",
    "utah": "Utah",
    "vt": "Vermont",
    "vermont": "Vermont",
    "va": "Virginia",
    "virginia": "Virginia",
    "wa": "Washington",
    "washington": "Washington",
    "wv": "West Virginia",
    "west virginia": "West Virginia",
    "wi": "Wisconsin",
    "wisconsin": "Wisconsin",
    "wy": "Wyoming",
    "wyoming": "Wyoming",
}

CITY_TO_STATE: dict[str, str] = {
    "san francisco": "California",
    "sf": "California",
    "san jose": "California",
    "los angeles": "California",
    "la": "California",
    "sacramento": "California",
    "san diego": "California",
    "oakland": "California",
    "new york city": "New York",
    "nyc": "New York",
    "brooklyn": "New York",
    "austin": "Texas",
    "dallas": "Texas",
    "houston": "Texas",
    "san antonio": "Texas",
    "seattle": "Washington",
    "bellevue": "Washington",
    "chicago": "Illinois",
    "boston": "Massachusetts",
    "cambridge": "Massachusetts",
    "atlanta": "Georgia",
    "miami": "Florida",
    "orlando": "Florida",
    "tampa": "Florida",
    "denver": "Colorado",
    "boulder": "Colorado",
    "phoenix": "Arizona",
    "tempe": "Arizona",
    "portland": "Oregon",
    "nashville": "Tennessee",
    "charlotte": "North Carolina",
    "raleigh": "North Carolina",
    "durham": "North Carolina",
    "philadelphia": "Pennsylvania",
    "pittsburgh": "Pennsylvania",
}


def infer_role_family(title: str, description: str = "") -> str:
    text = f"{title} {description}"
    normalized = _normalize(text)
    for label, keywords in ROLE_FAMILIES:
        if any(_keyword_matches(normalized, keyword) for keyword in keywords):
            return label
    words = [word for word in re.findall(r"[a-z0-9]+", _normalize(title)) if word not in _TITLE_STOP_WORDS]
    if words:
        return " ".join(word.capitalize() for word in words[:3])
    return "General"


def expand_role_preferences(roles: list[str] | tuple[str, ...]) -> set[str]:
    expanded: set[str] = set()
    role_labels = {label for label, _keywords in ROLE_FAMILIES}
    for role in roles:
        clean_role = str(role or "").strip()
        if not clean_role:
            continue
        expanded.add(clean_role)
        normalized = _normalize(clean_role)
        expanded.update(MAJOR_ROLE_ALIASES.get(normalized, ()))
        for label in role_labels:
            if _normalize(label) == normalized:
                expanded.add(label)
    return expanded


def normalize_location_group(location: str) -> str:
    normalized = _normalize(location)
    if not normalized or normalized in {"unknown", "unknown location"}:
        return "Unknown"
    if "remote" in normalized:
        return "Remote"

    parts = [_normalize(part) for part in str(location).split(",") if part.strip()]
    candidates = parts + re.findall(r"[a-z]{2,}(?: [a-z]{2,})?", normalized)
    for candidate in candidates:
        if candidate in STATE_NAMES:
            return STATE_NAMES[candidate]
        if candidate in CITY_TO_STATE:
            return CITY_TO_STATE[candidate]

    for city, state in CITY_TO_STATE.items():
        if re.search(rf"\b{re.escape(city)}\b", normalized):
            return state
    if any(value in normalized for value in ("united states", "usa", "u s", "us only", "nationwide")):
        return "United States"
    return str(location).strip() or "Unknown"


def location_matches(job_location: str, requested_location: str) -> bool:
    requested = str(requested_location or "").strip()
    if not requested:
        return True
    job_text = _normalize(job_location)
    requested_text = _normalize(requested)
    if requested_text in job_text:
        return True
    return normalize_location_group(job_location).lower() == normalize_location_group(requested).lower()


def location_filter_options(locations: list[str]) -> list[str]:
    values = {location.strip() for location in locations if str(location).strip()}
    values.update(normalize_location_group(location) for location in list(values))
    return sorted(values, key=lambda value: (value == "Unknown", value.lower()))


_TITLE_STOP_WORDS = {
    "i",
    "ii",
    "iii",
    "iv",
    "jr",
    "sr",
    "junior",
    "senior",
    "lead",
    "staff",
    "principal",
    "manager",
    "remote",
    "hybrid",
    "onsite",
    "on",
    "site",
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value).lower())).strip()


def _keyword_matches(normalized: str, keyword: str) -> bool:
    normalized_keyword = _normalize(keyword)
    return bool(re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized))
