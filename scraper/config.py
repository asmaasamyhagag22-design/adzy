"""Configuration constants for the scraper.

All tunable values live here. No magic numbers in the rest of the code.
"""
import os

# --- Scrape budget --------------------------------------------------
# MEASURED (2026-06-15, 32 saved sites): the 60s wall was the BINDING constraint on
# 21/32 sites — they hit it after only 1-7 subpages (JS pages render ~15s each), so
# the page cap of 7 was almost never reached and content-rich sites (median 17 HIGH
# pages) were badly under-covered. The LLM evidence pack is char-capped, so more
# pages cost crawl TIME only, not tokens. Raised for solid coverage; a slow site now
# takes ~2.5 min instead of ~1 min. Tune down for speed-over-coverage.
# 2026-07-07: owner wants MOST of the data captured and is fine with up to ~10 min per scrape.
# Raised 150/12 -> 540/40 so link-rich sites (institutes, directories, multi-service brands) go
# DEEP instead of stopping at 12 pages / 2.5 min. The budget is a CEILING, not a floor — a small
# site with few links still finishes fast (it exhausts its frontier well before the cap/time).
TOTAL_BUDGET_SECONDS = 540
MAX_INTERNAL_PAGES = 40  # in addition to homepage

# Adaptive crawl budget for E-COMMERCE (Pillar 1). MEASURED (benchmark/measure_ecom_*.py): a
# store discovers 100-300+ internal pages but the default 12-page / 150s budget reaches only
# ~12 of them (azzafahmy fresh scrape: 12 pages / 4% coverage) — AND the page cap and the time
# budget now bind at roughly the SAME point (~13 pages at ~11s/page render), so raising the
# page cap ALONE is a no-op; a store needs BOTH a higher cap AND a proportionally larger time
# budget. Triggered UNIVERSALLY when the homepage links + sitemap reveal many product-type URLs
# (a SIGNAL via the page-type classifier — never a vertical or a hardcoded name).
# 2026-07-06: raised 30/330 -> 50/560 because the owner needs INDIVIDUAL products captured, not
# just categories — the frontier now reserves most of the budget for product-DETAIL pages
# (crawler._reserve_product_quota), so the extra slots buy ~50 real products (name/price/image),
# not more collections. A store scrape now takes ~8-9 min; cutting the ~11s/page hard-coded
# homepage-footer waits for light sub-pages is the designed follow-up to bring that back down.
ECOMMERCE_MAX_INTERNAL_PAGES = 50
ECOMMERCE_BUDGET_SECONDS = 560        # ~50 pages at ~11s/page (cap and time bind together)
ECOMMERCE_PRODUCT_URL_MIN = 15        # >= this many PRODUCTS-type URLs (homepage+sitemap) -> store

# A crawl must NEVER end homepage-only when internal links exist: pre-crawl stages
# (dead sitemaps, a heavy homepage) can eat the whole time budget, and a budget check
# firing at subpage #1 produced a 1-page scrape of a 300-link store (measured on
# nahdionline.com -> no product pages -> "no prices" downstream). The budget guard is
# for stopping a long tail, not preventing the start.
MIN_SUBPAGE_ATTEMPTS = 5

# Minimum visible rendered text for a page to count as real brand content (P-61). Below this a
# page is treated as an EMPTY_RENDERED_DOM (a failed SPA / JS-wall / blank shell) — the OLD guard
# only fired when the HTML had no "<body" tag at all, which almost never happens, so empty shells
# (marasim: 0 clean chars) sailed through as pages_succeeded. Deliberately LOW: the audit finding
# guessed ~40, but a committed test fixture (a salvaged menu subpage, "Our Menu / grilled dishes
# and stews" = 33 chars) proves real-but-sparse content can sit in the low 30s — measurement beats
# the guess. 20 catches genuine emptiness (0 chars) and bare status/loading shells while keeping
# sparse real pages; the substantive "enough brand signal" judgment lives in readiness (P-62).
MIN_RENDERED_CHARS = 20

# --- Multi-depth crawl (FIX-A, 2026-07-11) ---------------------------
# The frontier used to be built ONCE from homepage links + sitemap, making every crawl
# structurally depth-1: the leaf layer (individual courses/services/menu items) that only
# appears on category pages was discovered but NEVER fetched (MEASURED on nti.sci.eg: the
# crawl stopped at 169s of its 540s budget with 13 page slots unused while courses.php?catID=…
# sat in links.internal). Links found on each fetched page now RE-SEED the frontier up to
# this depth (homepage=0). Bounded by the same page cap + time budget as before — the crawl
# just spends the budget it used to leave on the table. Owner OK'd larger crawls (latency).
MAX_CRAWL_DEPTH = 3
BFS_MAX_NEW_CANDIDATES = 120          # frontier-list growth bound per crawl (fetches still capped)

# Offering segments that mark an INDIVIDUAL leaf item (see classify.page_type.is_leaf_detail):
# a segment from this list followed by a slug (/courses/<x>), or an offering-named CMS file with
# an id-bearing query param (courses.php?catID=601). UNIVERSAL default — covers stores,
# restaurants, clinics, education, services; extend per-category only with owner sign-off.
LEAF_DETAIL_SEGMENTS = (
    "products", "product", "pdp",                    # stores (existing behavior, unchanged)
    "courses", "course", "programs", "program",      # education
    "tracks", "track", "modules", "module", "diplomas", "diploma",
    "services", "service", "treatments", "treatment",  # services / clinics
    "packages", "package", "plans", "plan",
    "menu", "item", "items", "dishes", "dish",       # restaurants
)

PAGE_TIMEOUT_MS = 20_000
NAV_TIMEOUT_MS = 25_000
INTER_PAGE_DELAY_SECONDS = 1.0

# --- Fetch retry (Tier 0 resilience) --------------------------------
# Transient failures (DNS blips, HTTP/2 resets, nav timeouts) get this many
# EXTRA attempts with linear backoff. Permanent outcomes (bot-block, 4xx,
# empty DOM, success) never retry. See fetcher.fetch_page.
RETRY_ATTEMPTS = 2                # total tries = 1 + RETRY_ATTEMPTS
RETRY_BACKOFF_SECONDS = 1.5       # sleep grows: 1.5s, 3.0s, ...
# The HOMEPAGE is make-or-break: a homepage HTTP 429 loses the ENTIRE scrape (0 pages ->
# "could not read this site"), while a rate-limit cooldown clears in tens of seconds. So the
# homepage gets a LARGER, 429-ONLY patience budget (6s,12s,18s,24s ~= 60s total) — every other
# failure and every subpage still fails fast on RETRY_ATTEMPTS. Politely waiting, never hammering.
HOMEPAGE_RATE_LIMIT_RETRIES = 4

# Two-phase image fetch (owner footprint ruling): on the FULL homepage, load above-fold images
# (logo/header/hero) then block BELOW-fold product-image BYTES during the scroll — URLs are still
# collected from the DOM. Cuts the product-image bulk with no content loss / no logo regression.
# `--thorough` sets this False to restore the old load-every-image behaviour for a rare hard case.
TWO_PHASE_IMAGE_FETCH = True

# --- Browser settings -----------------------------------------------
# A realistic current desktop-Chrome UA so normal sites don't reject an
# obviously-automated UA string. NOTE: this is the one Tier-0 honesty trade-off
# — we no longer self-identify as a bot in the UA. We STILL respect robots.txt
# and rate-limit politely (INTER_PAGE_DELAY_SECONDS); this is not stealth/evasion.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# LANGUAGE-NEUTRAL ACQUISITION (F-06 root cause). CS-158 registered the risk and deferred the
# measurement; it was MEASURED 2026-08-11 on alfouadpharmacies.com and the risk is real.
#
# The site negotiates on Accept-Language. Asking EN-first got `/en`, `<html lang=en>` and a crawl
# measured 100% English; asking AR-first got `/`, `<html lang=ar>` and 100% Arabic — same code,
# same site, same minute. Downstream then read "this brand is English" off a representation OUR
# OWN request had selected. A preference we send is not evidence about the brand.
#
# So the default is now NO preference: the server picks its own default representation and we
# observe what it chose. `None` means "send no Accept-Language at all", which is a different
# state from any wildcard or empty string — `*` and `""` are still preferences.
#
# Setting SCRAPER_ACCEPT_LANGUAGE opts INTO a preference for a run (a positive control, or an
# operator deliberately asking for one edition). Unlike before, that value now reaches the
# browser's top-level navigation too — see `fetcher._continue_with_language_policy`.
ACCEPT_LANGUAGE = os.getenv("SCRAPER_ACCEPT_LANGUAGE") or None

# The browser context locale is pinned rather than inherited. An unset Playwright locale takes the
# HOST MACHINE's locale, which would make the crawl's language depend on whose laptop it ran on.
# It drives `navigator.languages` only; the Accept-Language header is governed by ACCEPT_LANGUAGE.
BROWSER_LOCALE = os.getenv("SCRAPER_BROWSER_LOCALE", "en-US")
VIEWPORT_WIDTH = 1366
VIEWPORT_HEIGHT = 800

# --- URL params to strip during normalization -----------------------
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "ref_src", "yclid", "_ga",
    "mc_cid", "mc_eid", "igshid", "ttclid",
}

# --- Page type patterns (bilingual EN + AR) -------------------------
# Match against URL path AND anchor text. First match wins.
# Each tuple: (page_type, tier, [patterns])
PAGE_TYPE_PATTERNS = [
    ("contact", "HIGH", [
        "contact", "contact-us", "contactus", "get-in-touch", "reach-us",
        "find-us",
        "تواصل", "اتصل", "اتصال", "تواصل-معنا", "اتصل-بنا",
    ]),
    # CATERING before SERVICES: "/catering-services" should map to CATERING,
    # not SERVICES, because "services" is a substring match that would
    # otherwise win the first-match-wins lookup.
    ("catering", "HIGH", [
        "catering", "catering-services", "catering-menu",
        "ضيافة", "خدمات-الضيافة",
    ]),
    # BRANCHES_LOCATIONS before PRODUCTS: "/store-locator" contains "store"
    # (a PRODUCTS pattern) and would otherwise be misclassified.
    ("branches_locations", "HIGH", [
        "branches", "our-branches", "branch", "locations", "our-locations",
        "store-locator", "find-a-store",
        "فروع", "فروعنا", "الفروع", "مواقعنا",
    ]),
    # Cart / checkout SKIP must come BEFORE products so "/shopping-cart"
    # doesn't get classified as PRODUCTS via the "shop" pattern. These
    # are transactional UIs with no content worth extracting; we still
    # capture them as CTA *links* via the CTA verb list elsewhere.
    ("other", "SKIP", [
        "cart", "cart-details", "checkout", "basket", "my-cart",
        "shopping-cart", "view-cart",
        "عربة", "السلة", "سلة", "الدفع",
    ]),
    ("services", "HIGH", [
        "services", "service", "our-services", "what-we-do",
        "solutions", "offerings",
        "خدمات", "خدماتنا", "الخدمات",
    ]),
    ("products", "HIGH", [
        "products", "product", "our-products", "shop", "store",
        "catalog", "catalogue", "product-catalog",
        "collections", "collection",
        "best-sellers", "bestsellers", "new-arrivals",
        # PLP/PDP — the product-list/product-detail URL convention of enterprise
        # storefronts (measured on nahdionline.com: /ar-sa/plp/... URLs weren't
        # recognized as products, so the e-commerce crawl budget never triggered).
        "plp", "pdp",
        "منتجات", "منتجاتنا", "المنتجات", "المتجر", "متجر",
        "مجموعات",
    ]),
    ("menu", "HIGH", [
        # food / restaurant menus — keep the existing tag, expand patterns
        "menu", "menus", "the-menu", "our-menu", "food-menu",
        "restaurant-menu", "dishes", "meals", "food",
        "قائمة", "قائمة-الطعام", "قائمتنا",
        "المنيو", "منيو", "أطباق", "وجبات", "طعام", "أكلات",
    ]),
    ("delivery_order", "HIGH", [
        "order", "order-online", "order-now", "online-order",
        "delivery", "takeaway", "take-away", "takeout", "pickup",
        "اطلب", "اطلب-الآن", "توصيل", "ديليفري",
    ]),
    ("courses", "HIGH", [
        "courses", "our-courses", "course-list", "course", "all-courses",
        "كورسات", "دورات", "الدورات",
    ]),
    ("programs", "HIGH", [
        "programs", "programmes", "our-programs", "academic-programs",
        "training-programs", "tracks", "training",
        "برامج", "برامجنا", "تدريب",
    ]),
    ("admissions", "HIGH", [
        "admissions", "admission", "apply", "application",
        "how-to-apply", "enroll", "enrolment", "enrollment", "register-now",
        "التقديم", "التسجيل", "قدم", "سجل",
    ]),
    ("pricing", "HIGH", [
        "pricing", "prices", "plans", "rates", "packages", "fees",
        "أسعار", "الأسعار", "الباقات", "الاسعار", "تسعير",
    ]),
    ("booking", "HIGH", [
        "book", "booking", "reserve", "reservation", "appointment",
        "احجز", "حجز", "موعد", "احجز-الآن",
    ]),
    ("about", "MEDIUM", [
        "about", "about-us", "aboutus", "who-we-are", "company",
        "story", "our-story", "mission", "vision",
        "من-نحن", "عن-الشركة", "من_نحن", "نبذة", "رؤيتنا",
    ]),
    ("portfolio", "MEDIUM", [
        "portfolio", "our-work", "work", "projects", "our-projects",
        "case-studies", "case-study", "showcase",
        "أعمالنا", "مشاريعنا", "دراسات-الحالة",
    ]),
    ("testimonials", "MEDIUM", [
        "testimonials", "reviews", "client-reviews",
        "what-clients-say", "success-stories",
        "آراء", "آراء-العملاء", "شهادات", "قصص-نجاح",
    ]),
    ("events", "MEDIUM", [
        "events", "upcoming-events", "our-events", "event",
        "فعاليات", "أحداث", "الفعاليات",
    ]),
    ("offers", "MEDIUM", [
        "offers", "promotions", "deals", "special-offers", "discounts",
        "offer-detail", "offer-details",
        "عروض", "عروضنا", "خصومات",
    ]),
    ("faq", "MEDIUM", [
        "faq", "faqs", "questions", "help",
        "الأسئلة", "اسئلة-شائعة", "الاسئله",
    ]),
    ("gallery", "LOW", [
        "gallery", "photo-gallery", "photos", "our-gallery", "images",
        "معرض", "صور", "المعرض",
    ]),
    ("blog", "LOW", [
        "blog", "news", "articles", "posts",
        "مدونة", "أخبار", "مقالات",
    ]),
    ("careers", "LOW", [
        "career", "careers", "jobs", "join-us",
        "وظائف", "التوظيف",
    ]),
    ("legal", "SKIP", [
        "privacy", "terms", "tos", "legal", "cookie", "cookies",
        "خصوصية", "شروط", "الشروط",
    ]),
]

# --- CTA detection --------------------------------------------------
# Anchor texts (lowercased) that mark a link as a CTA candidate.
CTA_VERBS = {
    # English — general conversion verbs
    "book", "book now", "buy", "buy now", "shop", "shop now", "order",
    "order now", "order online",
    "get started", "get a quote", "request a quote",
    "sign up", "subscribe", "join", "register", "contact us", "call now",
    "call us",
    "schedule", "start", "try free", "try now", "download", "apply",
    "reserve", "reserve now", "claim", "learn more",
    # English — ECOMMERCE shop entry-points (added 2026-06-14 from Azza Fahmy:
    # the real shop CTAs were scraped as <a> ("Catalog", "VIEW ALL") but missed,
    # so the CTA degraded to a generic "Visit website" on a store. "shop *"/"order *"
    # already match via the startswith rule; these are the non-"shop" phrasings.)
    "catalog", "shop all", "view all", "browse", "explore the collection",
    "view collection", "shop the collection", "shop collection",
    # "explore *" (added 2026-07-03 from daturial "Explore Solutions"; MEASURED
    # corpus-wide: fires only on real action anchors — "Explore Now",
    # "EXPLORE OUR MENU", "Explore More Posts" — zero flooding on 87 scrapes).
    "explore", "استكشف",
    # English — restaurant/food-ordering CTAs (added 2026-05 from Buffalo Burger benchmark).
    # NOTE: "menu" alone is special-cased in _is_cta_anchor so it only matches
    # as an exact anchor, not as a substring of "menu of the day".
    "view menu", "menu", "see menu", "our menu",
    "cart", "go to cart", "view cart",
    "delivery", "takeaway", "pickup",
    "download app", "download our app", "get the app", "mobile app",
    # Arabic
    "احجز", "احجز الآن", "اطلب", "اطلب الآن", "اشتري", "اشترك", "سجل",
    "تواصل", "تواصل معنا", "اتصل", "اتصل بنا", "ابدأ", "جرب", "حمل",
    "احصل", "اطلب عرض", "اطلب عرض سعر",
    # Arabic — restaurant/food-ordering CTAs (added 2026-05)
    "اطلب دلوقتي", "حمل التطبيق", "حمّل التطبيق",
    "القائمة", "المنيو", "السلة", "عربة التسوق",
    "توصيل", "ديليفري",
    # Arabic — ecommerce shop entry-points (added 2026-06-14)
    "تسوق", "تسوّق", "تسوق الآن", "تصفح", "الكتالوج", "عرض الكل", "تسوق الكل",
    # H5 (2026-07-05) — major expansion-market languages (product is "any language, any
    # country"; a French store previously scored cta_candidates=0 on 310 internal links).
    # Grounded in elietop.com's real CTAs + common conversion verbs; MEASURED corpus-wide:
    # +CTAs only on the French site, ZERO inflation on the 100+ EN/AR scrapes. Italian
    # "registrati" was DROPPED — it is a prefix of English "Registration" (false match).
    # French
    "nous contacter", "contactez-nous", "contacter", "en savoir plus", "voir plus",
    "panier", "connectez-vous", "connexion", "continuer les achats", "acheter",
    "commander", "réserver", "découvrir", "s'inscrire", "ajouter au panier",
    # Spanish
    "contáctanos", "contactar", "comprar", "comprar ahora", "añadir al carrito",
    "carrito", "reservar", "saber más", "más información", "iniciar sesión",
    "regístrate", "descubrir", "ver más",
    # German
    "kontakt", "kontaktieren", "jetzt kaufen", "kaufen", "mehr erfahren", "anmelden",
    "warenkorb", "buchen", "bestellen", "entdecken", "registrieren",
    # Italian
    "contattaci", "acquista", "acquista ora", "scopri", "scopri di più", "scopri di piu",
    "prenota", "carrello", "iscriviti",
    # Portuguese
    "saiba mais", "fale conosco", "contato", "carrinho", "cadastre-se",
    "adicionar ao carrinho", "descubra",
    # Turkish
    "satın al", "iletişim", "bize ulaşın", "keşfet", "sepet", "sepete ekle",
    "rezervasyon", "kayıt ol", "üye ol",
}

# --- Social platforms -----------------------------------------------
# Map of (domain substring) -> platform name
SOCIAL_DOMAINS = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "linkedin.com": "linkedin",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "pinterest.com": "pinterest",
    "snapchat.com": "snapchat",
    "telegram.org": "telegram",
    "t.me": "telegram",
    "wa.me": "whatsapp",
    "whatsapp.com": "whatsapp",
    "threads.net": "threads",
    "github.com": "github",
}

# --- Bot protection indicators --------------------------------------
# Text phrases shown ON a challenge/block page. The scraper sends an EN-first Accept-Language, so
# most WAF interstitials come back in English — but a site that forces its own locale serves the
# challenge in that language, which the old EN-only list missed (P-63, D-8). A few common
# non-English phrasings are added; the language-INDEPENDENT signals (HTML markers, WAF headers,
# status+empty-body) in fetcher._detect_bot_protection are the real backstop.
BOT_PROTECTION_TEXT_SIGNALS = [
    "verify you are human",
    "checking your browser",
    "cf-challenge",
    "captcha",
    "are you a robot",
    "access denied",
    "blocked by",
    "ddos protection",
    "just a moment",
    "please enable javascript and cookies",
    # non-English challenge phrasings (Cloudflare/WAF localizations)
    "تأكد من أنك إنسان",          # ar: verify you are human
    "التحقق من أنك لست روبوت",     # ar: verify you are not a robot
    "لحظة من فضلك",               # ar: just a moment
    "vérifiez que vous êtes humain",  # fr
    "un instant",                 # fr: just a moment
    "insan olduğunuzu doğrulayın",    # tr
    "bir dakika",                 # tr: just a moment
    "verifique que es un humano", # es
    "überprüfung ihres browsers", # de: checking your browser
]

# Language-INDEPENDENT WAF/bot markers found in a challenge page's HTML or response headers,
# regardless of the display language (Cloudflare always bootstraps its challenge JS; Incapsula /
# PerimeterX / Akamai / Distil leave their own fingerprints). This is the real fix for P-63.
# CONCLUSIVE: these load ONLY on a challenge/interstitial page. Their presence IS the block,
# whatever the page's language or length.
BOT_CHALLENGE_MARKERS = [
    "/cdn-cgi/challenge-platform",   # Cloudflare managed challenge / Turnstile bootstrap
    "challenge-platform",
    "__cf_chl_",                     # Cloudflare challenge token
    "cf-chl-",
    "distil_r_captcha",              # Distil / Imperva
    "px-captcha",                    # PerimeterX
]

# PRESENCE ONLY: a vendor's script or cookie that ships on EVERY page it protects — including the
# ones served happily to real users. MEASURED (pepsi.com, 2026-07-21): the site answered 200 with a
# full 35KB page titled "Pepsi Official Site" and we discarded the entire site because `_incapsula_`
# appeared in the HTML. These count only when the page ALSO looks like an interstitial.
BOT_VENDOR_MARKERS = [
    "_incapsula_",                   # Imperva Incapsula
    "_pxhd",                         # PerimeterX
    "ak_bmsc",                       # Akamai Bot Manager cookie/marker
]

# Kept for callers that want every marker (the union), e.g. diagnostics.
BOT_PROTECTION_HTML_MARKERS = BOT_CHALLENGE_MARKERS + BOT_VENDOR_MARKERS

# --- Regex patterns -------------------------------------------------
EMAIL_REGEX = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

# --- Output dirs ----------------------------------------------------
# WHERE THE SCRAPES LIVE — one resolver, so nobody has to remember four defaults.
#
# The corpus is 3.4 GB across 12k files and it was sitting inside the owner's Google-Drive folder,
# where every stat() goes through the sync layer: a `du` over it did not finish in eight minutes
# and a single `find` timed out at five. Nothing in it is committed (`.gitignore`: "Scrape outputs
# (large, regeneratable)"), so the whole corpus can live on a local disk while the code stays on
# the synced drive.
#
# A junction was the obvious fix and the Drive filesystem REFUSES it ("Incorrect function" —
# reparse points are not implemented there), which is why this is a config value and not a symlink.
# Unset -> "scrapes", byte-identical to every previous run.
DEFAULT_OUTPUT_ROOT = "scrapes"


def scrapes_root() -> str:
    """The corpus root, resolved AT CALL TIME.

    Read here rather than pinned at import because `.env` is loaded by the entry points, and a
    constant computed at import would be frozen before `load_dotenv` ever ran — the variable would
    be set, ignored, and every scrape would silently write to the old place.
    """
    return (os.environ.get("BASEERA_SCRAPES_DIR") or "").strip() or DEFAULT_OUTPUT_ROOT

# --- Image / asset ----
MAX_PALETTE_COLORS = 6