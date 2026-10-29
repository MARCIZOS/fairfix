"""The fixed catalogue of standard jobs that bill lines are mapped to.

Every bill line is normalised to one of these job ids, or to "unrecognised".
The `kind` of job decides which checks apply:

- maintenance : normally replaced on a schedule (km and/or months)
- wear_repair : replaced on condition after inspection (brakes, clutch ...)
- add_on      : optional treatments that are not part of routine schedules
- misc        : small consumables the schedule check does not cover
- labour      : labour or service charges

`aliases` are lower-case fragments that commonly appear on Indian service
bills. They give a deterministic first pass before any model is asked.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

UNRECOGNISED = "unrecognised"


@dataclass(frozen=True)
class Job:
    id: str
    label: str
    kind: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


JOBS: tuple[Job, ...] = (
    # --- maintenance: scheduled replacements -------------------------------
    Job("engine_oil_replacement", "Engine oil replacement", "maintenance",
        ("engine oil", "eng oil", "engn oil", "e/oil", "इंजन ऑयल", "इंजन तेल", "इंजिन ऑयल", "motor oil", "oil change", "10w30", "10w-30", "5w30", "5w-30", "0w20", "0w-20", "10w40", "10w-40")),
    Job("oil_filter_replacement", "Oil filter replacement", "maintenance",
        ("oil filter", "oil fltr", "oil cartridge", "ऑयल फिल्टर")),
    Job("air_filter_replacement", "Air filter replacement", "maintenance",
        ("air filter", "air fltr", "air clnr", "एयर फिल्टर", "एअर फिल्टर", "air cleaner", "air element", "air clnr element")),
    Job("fuel_filter_replacement", "Fuel filter replacement", "maintenance",
        ("fuel filter", "fuel fltr", "diesel filter")),
    Job("cabin_filter_replacement", "Cabin / AC filter replacement", "maintenance",
        ("cabin filter", "ac filter", "pollen filter", "a/c filter")),
    Job("spark_plug_replacement", "Spark plug replacement", "maintenance",
        ("spark plug", "sparkplug", "plug spark", "spk plg", "spk plug", "s/plug", "स्पार्क प्लग")),
    Job("coolant_replacement", "Coolant replacement", "maintenance",
        ("coolant", "antifreeze", "radiator fluid", "कूलेंट", "कूलैंट")),
    Job("brake_fluid_replacement", "Brake fluid replacement", "maintenance",
        ("brake fluid", "dot 3", "dot3", "dot 4", "dot4")),
    Job("transmission_oil_replacement", "Transmission / gear oil replacement", "maintenance",
        ("gear oil", "transmission oil", "transmission fluid", "atf", "cvt fluid", "transmission oil")),
    Job("drive_belt_replacement", "Drive / V-belt replacement", "maintenance",
        ("drive belt", "v-belt", "v belt", "vbelt", "alternator belt")),
    Job("timing_belt_replacement", "Timing belt replacement", "maintenance",
        ("timing belt",)),
    Job("chain_lubrication", "Chain cleaning and lubrication", "maintenance",
        ("chain lube", "chain lubrication", "chain cleaning", "chain clean")),
    Job("valve_clearance_adjustment", "Valve clearance adjustment", "maintenance",
        ("valve clearance", "tappet")),
    Job("throttle_body_cleaning", "Throttle body cleaning", "maintenance",
        ("throttle body",)),
    Job("periodic_service_labour", "Periodic service labour", "labour",
        ("periodic service", "periodic maintenance", "paid service", "free service", "general service", "service charge", "pms", "labour charge", "labor charge", "gen srvc", "general srvc", "srvc lbr", "service lbr", "सर्विस चार्ज", "सर्विस लेबर", "सर्विसिंग")),

    # --- wear_repair: replaced on condition ---------------------------------
    Job("brake_pad_replacement", "Brake pad replacement", "wear_repair",
        ("brake pad", "disc pad", "brake pads", "ब्रेक पैड")),
    Job("brake_shoe_replacement", "Brake shoe replacement", "wear_repair",
        ("brake shoe", "brake shoes", "shoe brake", "ब्रेक शू")),
    Job("brake_disc_replacement", "Brake disc / rotor replacement", "wear_repair",
        ("brake disc", "rotor", "disc rotor")),
    Job("brake_drum_replacement", "Brake drum replacement", "wear_repair",
        ("brake drum", "drum brake")),
    Job("clutch_plate_replacement", "Clutch plate replacement", "wear_repair",
        ("clutch plate", "clutch disc", "clutch kit", "clutch assy")),
    Job("chain_sprocket_replacement", "Chain and sprocket kit replacement", "wear_repair",
        ("chain sprocket", "chain kit", "sprocket")),
    Job("battery_replacement", "Battery replacement", "wear_repair",
        ("battery",)),
    Job("tyre_replacement", "Tyre replacement", "wear_repair",
        ("tyre", "tire")),
    Job("wiper_blade_replacement", "Wiper blade replacement", "wear_repair",
        ("wiper blade", "wiper")),
    Job("suspension_repair", "Suspension / shock absorber repair", "wear_repair",
        ("shock absorber", "suspension", "front fork", "fork oil", "strut")),
    Job("bearing_replacement", "Wheel / steering bearing replacement", "wear_repair",
        ("bearing",)),
    Job("ac_gas_refill", "AC gas top-up / refill", "wear_repair",
        ("ac gas", "a/c gas", "refrigerant", "r134a", "r-134a", "r1234yf")),

    # --- add_on: optional treatments ----------------------------------------
    Job("engine_flush", "Engine flush", "add_on",
        ("engine flush", "eng flush", "flush oil", "engine flushing")),
    Job("fuel_additive", "Fuel system additive / injector cleaner", "add_on",
        ("fuel additive", "injector clean", "fuel system clean", "fuel treatment", "petrol additive", "diesel additive")),
    Job("engine_oil_additive", "Engine oil additive", "add_on",
        ("oil additive", "engine additive", "eng additive", "addtv")),
    Job("ac_disinfection", "AC disinfection / sanitisation", "add_on",
        ("ac disinfect", "ac sanitis", "ac sanitiz", "ac deodor", "evaporator clean")),
    Job("anti_rust_coating", "Anti-rust / underbody coating", "add_on",
        ("anti rust", "anti-rust", "underbody", "rust coat", "under body")),
    Job("teflon_coating", "Paint protection / Teflon / ceramic coating", "add_on",
        ("teflon", "ceramic", "paint protection", "polish coating")),
    Job("radiator_flush", "Radiator flush", "add_on",
        ("radiator flush", "radiator clean")),
    Job("interior_cleaning", "Interior dry clean / vacuum", "add_on",
        ("dry clean", "interior clean", "vacuum", "upholstery")),
    Job("wheel_alignment_balancing", "Wheel alignment and balancing", "wear_repair",
        ("alignment", "balancing", "wheel balance")),

    # --- misc: consumables not covered by the schedule check ---------------
    Job("washer_fluid", "Washer fluid", "misc",
        ("washer fluid", "wiper fluid", "washer liquid")),
    Job("bulb_replacement", "Bulb replacement", "misc",
        ("bulb", "lamp")),
    Job("fuse_replacement", "Fuse replacement", "misc",
        ("fuse",)),
    Job("washing", "Vehicle wash", "misc",
        ("wash", "foam wash", "water wash", "wsh", "धुलाई", "वॉश")),
    Job("consumables_misc", "Shop consumables (cotton waste, grease, cleaner)", "misc",
        ("consumable", "cotton waste", "grease", "shop supplies", "sundries", "cleaner")),
)

JOBS_BY_ID: dict[str, Job] = {j.id: j for j in JOBS}


def get_job(job_id: str) -> Job | None:
    return JOBS_BY_ID.get(job_id)


def _fold(text: str) -> str:
    """Lower-case, hyphens to spaces, and Devanagari nukta removed, so that
    'फ़िल्टर' and 'फिल्टर' (both common on bills) match the same alias."""
    t = unicodedata.normalize("NFD", text or "").replace("\u093c", "")
    t = unicodedata.normalize("NFC", t)
    return " ".join(t.lower().replace("-", " ").split())


def alias_match(raw_text: str) -> str | None:
    """Deterministic first-pass mapping from bill text to a job id.

    Returns a job id only when exactly one job's aliases match; ambiguous or
    unmatched text returns None so the caller can ask the model or mark the
    line unrecognised. Longest alias wins ties within a single job.
    """
    text = _fold(raw_text)
    hits: dict[str, int] = {}
    for job in JOBS:
        for alias in job.aliases:
            a = _fold(alias)
            if a and a in text:
                hits[job.id] = max(hits.get(job.id, 0), len(a))
    if not hits:
        return None
    best = max(hits.values())
    winners = [jid for jid, n in hits.items() if n == best]
    if len(winners) != 1:
        return None
    # Guard against a short generic alias beating a clear longer phrase for
    # another job: require the winner to be clearly longer than runners-up.
    runners = sorted(hits.values(), reverse=True)
    if len(runners) > 1 and runners[1] >= best - 2:
        return None
    return winners[0]


# Line type to assume when the bill doesn't say (bills rarely do). Only jobs with a buyable
# item get "part"/"consumable", which is what lets the price search compare them; services
# such as washing or alignment stay "unknown" and are never price-compared.
_CONSUMABLE_JOBS = {"engine_oil_replacement", "brake_fluid_replacement", "coolant_replacement",
                    "transmission_oil_replacement", "engine_flush", "radiator_flush", "engine_oil_additive",
                    "fuel_additive", "washer_fluid"}
_PART_JOBS = {"oil_filter_replacement", "air_filter_replacement", "fuel_filter_replacement", "cabin_filter_replacement",
              "spark_plug_replacement", "drive_belt_replacement", "timing_belt_replacement", "battery_replacement",
              "bearing_replacement", "brake_disc_replacement", "brake_drum_replacement", "brake_pad_replacement",
              "brake_shoe_replacement", "chain_sprocket_replacement", "clutch_plate_replacement", "tyre_replacement",
              "wiper_blade_replacement", "bulb_replacement", "fuse_replacement"}


def inferred_kind(job_id: str | None) -> str:
    job = get_job(job_id or "")
    if not job:
        return "unknown"
    if job.kind == "labour":
        return "labour"
    if job.id in _CONSUMABLE_JOBS:
        return "consumable"
    if job.id in _PART_JOBS:
        return "part"
    return "unknown"
