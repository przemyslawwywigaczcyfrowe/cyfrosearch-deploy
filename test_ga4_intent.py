"""Test all GA4 search queries (1-300) for intent correctness."""
from search_engine import suggest, warm_caches

warm_caches()

# GA4 queries with expected intent
queries = {
    # Camera bodies
    "canon r6": "Canon EOS R6",
    "sony a7": "Sony A7 camera",
    "nikon z8": "Nikon Z8",
    "r5": "Canon EOS R5",
    "sony a7 iv": "Sony A7 IV",
    "a7 v": "Sony A7 V",
    "sony a7 v": "Sony A7 V",
    "canon r5": "Canon EOS R5",
    "nikon z6": "Nikon Z6",
    "r8": "Canon EOS R8",
    "x-t5": "Fujifilm X-T5",
    "r6": "Canon EOS R6",
    "canon r10": "Canon EOS R10",
    "x-e5": "Fujifilm X-E5",
    "R7": "Canon EOS R7",
    "a6700": "Sony A6700",
    "a7 iv": "Sony A7 IV",
    "canon r50": "Canon EOS R50",
    "canon r7": "Canon EOS R7",
    "canon rp": "Canon EOS RP",
    "fx30": "Sony FX30",
    "r10": "Canon EOS R10",
    "sx740": "Canon SX740",
    "x-m5": "Fujifilm X-M5",
    "z50": "Nikon Z50",
    "z6 III": "Nikon Z6 III",
    "r5 ii": "Canon EOS R5 Mark II",
    "r6 III": "Canon EOS R6 Mark III",
    "canon r6 mark ii": "Canon EOS R6 Mark II",
    "canon r8": "Canon EOS R8",
    "om-5": "OM System OM-5",
    "Canon EOS 5D Mark IV": "Canon 5D Mark IV",
    "r6mark": "Canon R6 Mark",
    "Sony fx3": "Sony FX3",
    "Sony zv": "Sony ZV series",
    "d850": "Nikon D850",
    "fujifilm x-t5": "Fujifilm X-T5",
    "leica q3": "Leica Q3",
    "nikon z5": "Nikon Z5",
    "nikon z5ii": "Nikon Z5II",
    "nikon z7": "Nikon Z7",
    "nikon zr": "Nikon ZR",
    "r50": "Canon EOS R50",
    "rp": "Canon EOS RP",
    "sony a1": "Sony A1",
    "sony fx3": "Sony FX3",
    "x-t30": "Fujifilm X-T30",
    "x-t30 iii": "Fujifilm X-T30 III",
    "x-t50": "Fujifilm X-T50",
    "z6": "Nikon Z6",
    "Atomos schinobi": "Atomos Shinobi monitor",
    "Canon g7": "Canon G7X camera",
    "Dji": "DJI products",
    "EOS R10": "Canon EOS R10",
    "R3": "Canon EOS R3",
    "Sony a7iii": "Sony A7 III",
    "Sony rx100": "Sony RX100",
    "X-e5": "Fujifilm X-E5",
    "a6100": "Sony A6100",
    "a6400": "Sony A6400",
    "eos r6": "Canon EOS R6",
    "fx2": "Sony FX2",
    "nikon zf": "Nikon Zf",
    "r6 II": "Canon R6 Mark II",
    "r6 iii": "Canon R6 Mark III",
    "r7": "Canon EOS R7",
    "z50 ii": "Nikon Z50 II",
    "z9": "Nikon Z9",
    "Nikon z": "Nikon Z-series camera",
    "Canon eos r50": "Canon EOS R50",
    "Nikon d850": "Nikon D850",
    "Nikon d750": "Nikon D750",
    "Nikon z50": "Nikon Z50",
    "Nikon z6 II": "Nikon Z6 II",
    "Nikon z7": "Nikon Z7",
    "Nikon zr": "Nikon ZR",
    "Hasselblad": "Hasselblad camera",
    "Fujifilm x-t30": "Fujifilm X-T30",
    "Fujifilm x100vi": "Fujifilm X100VI",
    "Sony a6400": "Sony A6400",
    "Canon R6 Mark III": "Canon EOS R6 Mark III",
    "fujifilm x100": "Fujifilm X100 series camera",
    "sony 6700": "Sony A6700",
    "a7IV": "Sony A7 IV",
    "a7v": "Sony A7 V",
    "sony a7v": "Sony A7 V",
    "canon g7x": "Canon G7X",
    "gh7": "Panasonic GH7",
    "lumix s9": "Panasonic Lumix S9",
    "sony a7r V": "Sony A7R V",
    "x-s20": "Fujifilm X-S20",
    "gfx": "Fujifilm GFX",
    "Fujifilm x t5": "Fujifilm X-T5 (space in model)",
    "Canon Pixma G640": "Canon Pixma G640 printer",
    "D7500": "Nikon D7500",
    "Fuji x": "Fujifilm X-series",
    "Fuji x-e5": "Fujifilm X-E5",
    "fuji": "Fujifilm products",
    "c50": "Canon EOS C50",
    "c80": "Canon EOS C80",
    "xf605": "Canon XF605",
    "Eizo cg2700": "Eizo CG2700 monitor",

    # Lenses
    "sigma 24-70": "Sigma 24-70mm",
    "28-70": "28-70mm lens",
    "sony 70-200": "Sony 70-200mm",
    "24-105": "24-105mm lens",
    "24-120": "24-120mm lens",
    "24-70": "24-70mm lens",
    "sony 24-70": "Sony 24-70mm GM",
    "40-150": "OM 40-150mm",
    "200-600": "Sony 200-600mm",
    "200-800": "Canon 200-800mm",
    "28-75": "Tamron 28-75mm",
    "100-400": "Canon 100-400mm",
    "18-105": "Sony 18-105mm",
    "Sigma 24": "Sigma 24mm+",
    "Sigma 35": "Sigma 35mm",
    "Sony 24-70": "Sony 24-70mm GM",
    "canon 24-105": "Canon 24-105mm",
    "canon 70-200": "Canon 70-200mm",
    "rf 100-500": "Canon RF 100-500mm",
    "rf 70-200": "Canon RF 70-200mm",
    "24-70 gm2": "Sony 24-70mm GM2",
    "35-150": "35-150mm lens",
    "70-200": "70-200mm lens",
    "Tamron 28": "Tamron 28-xxx",
    "rf 24-105": "Canon RF 24-105mm",
    "rf 85": "Canon RF 85mm",
    "sigma 28-45": "Sigma 28-45mm",
    "tamron 150-500": "Tamron 150-500mm",
    "tamron 90": "Tamron 90mm macro",
    "canon 24-70": "Canon 24-70mm",
    "canon rf 35 1.4": "Canon RF 35mm f/1.4L",
    "rf 100-400": "Canon RF 100-400mm",
    "rf 35": "Canon RF 35mm",
    "rf 50": "Canon RF 50mm",
    "sigma 20-200": "Sigma 20-200mm",
    "tamron 35-150": "Tamron 35-150mm",
    "fe 24-105": "Sony FE 24-105mm",
    "12-35 f2.8": "Panasonic 12-35 f/2.8",
    "12-40": "OM 12-40mm",
    "14-35": "14-35mm lens",
    "70-200 2.8": "70-200mm f/2.8",
    "70-200 f4": "70-200mm f/4",
    "70-200 sony": "Sony 70-200mm",
    "300 mm f2.8 sony": "Sony 300mm f/2.8",
    "24-105 2.8": "24-105 f/2.8",
    "50 1.2": "50mm f/1.2",
    "90 macro": "90mm macro",
    "75-300": "75-300mm lens",
    "Sony 24-105": "Sony 24-105mm",
    "Tamron 28-75": "Tamron 28-75mm",
    "Tamron 150-500 Nikon Z": "Tamron 150-500 Nikon Z",
    "Tamron 17-70": "Tamron 17-70mm",
    "Tamron 35": "Tamron 35mm+",
    "Tamron 70-180": "Tamron 70-180mm",
    "sigma 150-600": "Sigma 150-600mm",
    "sigma 17-40": "Sigma 17-40mm",
    "sony 16-35": "Sony 16-35mm",
    "tamron 20-40": "Tamron 20-40mm",
    "rf 35 1.4": "Canon RF 35mm f/1.4L",
    "rf 85 1.4": "Canon RF 85mm f/1.4L",
    "rf 28": "Canon RF 28mm",
    "rf 45": "Canon RF 45mm",
    "rf 45 mm": "Canon RF 45mm",
    "sony 35 1.4 gm": "Sony 35mm f/1.4 GM",
    "sony 50-150": "Sony 50-150mm",
    "Viltrox 27": "Viltrox 27mm",
    "xf 23": "Fujifilm XF 23mm",
    "Sigma 50": "Sigma 50mm",
    "Sigma 85": "Sigma 85mm",
    "Canon 28-70": "Canon 28-70mm",
    "Canon 35": "Canon 35mm",
    "Canon RF 28-70mm f/2": "Canon RF 28-70mm f/2 L",
    "16-30": "Tamron 16-30mm",
    "16-55": "Fujifilm 16-55mm",
    "18-150": "Canon 18-150mm",
    "18-50": "Sigma 18-50mm",
    "180-600": "Nikon 180-600mm",
    "24-70 rf": "Canon RF 24-70mm",
    "50-140": "Fujifilm 50-140mm",
    "70-180": "70-180mm lens",
    "tamron 16-30": "Tamron 16-30mm",

    # Accessories / misc
    "np-fz100": "Sony NP-FZ100 battery",
    "Tamron": "Tamron lenses",
    "botis 400": "GlareOne Botis 400",
    "botis 200": "GlareOne Botis 200",
    "botis": "GlareOne Botis",
    "dji mic mini": "DJI Mic Mini",
    "godox": "Godox flash/light",
    "leica": "Leica products",
    "ricoh": "Ricoh GR",
    "sony": "Sony popular products",
    "torba": "camera bag",
    "statyw": "tripod/stand",
    "canon": "Canon popular products",
    "gimbal": "gimbal stabilizer",
    "godox x3": "Godox X3 trigger",
    "lexar": "Lexar cards",
    "stork 295": "GlareOne Stork 295",
    "peak design": "Peak Design products",
    "dji mic": "DJI microphone",
    "godox mf-r76": "Godox MF-R76 macro flash",
    "karta": "memory card",
    "longbow": "GlareOne Longbow",
    "mathorn": "Mathorn battery",
    "adapter": "mount adapter",
    "g640": "Canon Pixma G640",
    "davinci": "DaVinci Resolve",
    "tele": "teleconverter",
    "vega 400": "GlareOne Vega 400",
    "x3": "Godox X3",
    "x5": "Insta360 X5",
    "Adapter ef": "Canon EF adapter",
    "Beauty": "beauty dish",
    "Czytnik": "card reader",
    "DMW-BLC12E": "Panasonic battery",
    "EN-EL15c": "Nikon battery",
    "Filtr uv 67": "UV filter 67mm",
    "Godox v1": "Godox V1 flash",
    "Om": "OM System",
    "Pasek": "camera strap",
    "Samyang": "Samyang lenses",
    "Smallrig": "SmallRig accessories",
    "Softbox": "softbox",
    "Sony NP-FZ100": "Sony battery",
    "Statyw": "tripod/stand",
    "Tether": "TetherTools cable",
    "Gimbal": "gimbal",
    "ftz ii": "Nikon FTZ II adapter",
    "lantern": "Godox lantern",
    "lenspen": "LensPen cleaning",
    "mikrofon": "microphone",
    "monopod": "monopod",
    "peak": "Peak Design",
    "prompter": "teleprompter",
    "strumienica": "snoot",
    "super white": "Savage white backdrop",
    "v100": "Godox V100",
    "Czytnik kart": "card reader",
    "Drukarka": "printer",
    "CFexpress": "CFexpress card",
    "Adapter ef rf": "Canon EF-RF adapter",
    "filtr nd": "ND filter",
    "kubek": "mug",
    "Botis": "GlareOne Botis",
    "BLS-50": "Olympus BLS-50 battery",
    "BC-QZ1": "Sony BC-QZ1 charger",
    "EN-EL25a": "Nikon EN-EL25a battery",
    "fz100": "Sony NP-FZ100",
    "lowepro 450": "Lowepro bag 450",
    "raynox": "Raynox macro adapter",
    "sirui": "Sirui tripod",
    "sony a6": "Sony A6xxx",
    "amaran 150c": "Amaran 150c light",
    "8x42": "binoculars 8x42",
    "air 360": "360 product",
    "ak-r1": "Godox AK-R1 accessory",
    "boom": "boom arm",
    "elfo": "Elfo radio flash",
    "sluchawki": "headphones",
    "Viltrox 27": "Viltrox 27mm lens",
    "dji action 6": "DJI Action 6",
    "dji mic 3": "DJI Mic 3",
    "dji osmo mobile 8": "DJI Osmo Mobile 8",
    "dji rs 5": "DJI RS 5 gimbal",
    "godox k2": "Godox K2 macro kit",
    "r6 ma": "Canon R6 Mark (partial)",
    "r6 mark III": "Canon EOS R6 Mark III",
    "Canon rf 70-": "Canon RF 70-200mm",
    "typ e": "Type E bracket/cup",
    "botis 80": "GlareOne Botis 80",
    "dji": "DJI products",
    "dji action 6": "DJI Action 6",
}

ACCESSORY_WORDS = [
    "klatka", "cage", "grip", "pokrywka", "pasek", "etui",
    "kabel", "akumulator", "battery", "uchwyt", "plytka",
    "oslona", "GGS", "wyzwalacz", "zacisk", "spiralny",
]

issues = []
for q, expected in queries.items():
    r = suggest(q, size=8)
    prods = r.get("products", [])

    if not prods:
        issues.append((q, expected, "NO RESULTS", "-"))
        print(f"!!! [{q}] NO RESULTS (wanted: {expected})")
        continue

    top_name = prods[0].get("name", "")
    top_brand = prods[0].get("brand", "?")
    top3 = [p.get("name", "")[:50] for p in prods[:3]]
    problem = None

    # Check: camera body queries should not return accessories
    camera_keywords = [
        "r6", "r5", "r8", "r10", "r50", "r7", "r3", "rp",
        "z8", "z6", "z5", "z7", "z9", "zf", "zr", "z50",
        "a7", "a6", "a1", "a9", "fx3", "fx6", "fx2", "fx30",
        "x-t5", "x-e5", "x-m5", "x-t30", "x-t50", "x-s20",
        "d850", "d750", "d7500", "q3", "gh7", "gfx",
        "rx100", "sx740", "g7x", "g7", "om-5",
    ]
    is_camera_query = any(kw in q.lower() for kw in camera_keywords)
    if is_camera_query:
        is_accessory = any(w.lower() in top_name.lower() for w in ACCESSORY_WORDS)
        if is_accessory:
            problem = f"ACCESSORY instead of camera: {top_name[:45]}"

    # Check: Sony a7iii should be A7 III not A7 (original)
    if "a7iii" in q.lower() or "a7 iii" in q.lower():
        if "III" not in top_name and "A7III" not in top_name and "ILCE7M3" not in top_name:
            if "s.n." in top_name or "(ILCE-7)" in top_name:
                problem = f"A7 original instead of A7 III: {top_name[:45]}"

    # Check: lens f-stop specificity
    if "1.4" in q and ("f/1.8" in top_name or "f/1.2" in top_name):
        if "1.4" not in top_name and "f/1.4" not in top_name:
            problem = f"Wrong f-stop in top result: {top_name[:45]}"

    if problem:
        issues.append((q, expected, problem, top_name[:50]))
        print(f"??? [{q}] {problem}")
    else:
        print(f"  OK [{q}] -> {top_brand} | {top_name[:50]}")

print(f"\n{'='*60}")
print(f"SUMMARY: {len(queries)} queries tested, {len(issues)} issues found")
print(f"{'='*60}")
if issues:
    for q, expected, prob, top in issues:
        print(f"  ISSUE: \"{q}\"")
        print(f"    Expected: {expected}")
        print(f"    Problem: {prob}")
        print(f"    Got: {top}")
        print()
else:
    print("  ALL QUERIES PASS INTENT VALIDATION!")
