"""
Elasticsearch index mapping for Cyfrowe.pl products.

Design principles:
- Universal mapping — no category-specific field treatment
- Polish analyzer for text fields (name, description)
- Keyword sub-fields for exact matching and aggregations
- Numeric fields for price-based sorting/filtering
- GA popularity fields (ga_views, ga_sales) for function_score boosting
- Comprehensive synonym support (brand aliases + category synonyms)

SYNONYM SOURCES:
  brand_aliases.json  — 202 brand typo/alias rules (e.g., "cannon" → "canon")
  taxon_aliases.json  — 230 category synonym rules (e.g., "lustrzanka" = "dslr")
  These files are loaded by get_index_settings() and embedded into the synonym filter.

PRODUCTION RECOMMENDATION:
  Install the analysis-stempel plugin for proper Polish stemming.
  This would eliminate the need for match_phrase_prefix workaround
  in query building and improve recall for all Polish word inflections.
"""

import json
import os


def _load_synonyms() -> list[str]:
    """
    Load synonym rules from brand_aliases.json and taxon_aliases.json.

    Brand aliases are ONE-DIRECTIONAL (typo => canonical):
      "cannon, kanon, conon => canon"
    Taxon aliases are BIDIRECTIONAL (equivalencies):
      "lustrzanka, dslr, lustrzanka cyfrowa"

    Returns list of ES synonym rule strings.
    """
    base_dir = os.path.dirname(__file__)
    synonyms = []

    # --- Brand aliases: typo/variant → canonical brand name ---
    brand_path = os.path.join(base_dir, "brand_aliases.json")
    if os.path.exists(brand_path):
        with open(brand_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            brand = entry["brand"].lower()
            aliases = [a.strip().lower() for a in entry["aliases"]
                       if a.strip().lower() and a.strip().lower() != brand]
            if aliases:
                synonyms.append(", ".join(aliases) + " => " + brand)

    # --- Taxon aliases: bidirectional equivalencies ---
    taxon_path = os.path.join(base_dir, "taxon_aliases.json")
    if os.path.exists(taxon_path):
        with open(taxon_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            aliases = [a.strip().lower() for a in entry["aliases"]
                       if a.strip().lower()]
            if len(aliases) >= 2:
                synonyms.append(", ".join(aliases))

    # --- Core photo terminology (always present) ---
    core_synonyms = [
        "obiektyw, lens",
        "aparat, kamera, camera",
        "statyw, tripod",
        "lampa, flash, blyskowa",
        "filtr, filter",
        "plecak, backpack",
        "torba, bag",
        "mikrofon, mic",
        "bateria, akumulator, battery",
    ]
    synonyms.extend(core_synonyms)

    return synonyms


def get_index_settings() -> dict:
    """Return complete ES index settings with dynamically loaded synonyms."""
    synonyms = _load_synonyms()

    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "filter": {
                    "polish_stop": {
                        "type": "stop",
                        "stopwords": [
                            "i", "w", "z", "na", "do", "o", "od", "po", "za",
                            "ze", "dla", "nie", "tak", "to", "jest", "się",
                            "co", "jak", "ale", "lub", "czy", "że", "go",
                            "przez", "jej", "jego", "ich", "ten", "ta",
                            "te", "tym", "tego", "tej", "tych",
                        ],
                    },
                    "edge_ngram_filter": {
                        "type": "edge_ngram",
                        "min_gram": 2,
                        "max_gram": 15,
                    },
                    "synonym_filter": {
                        "type": "synonym",
                        "lenient": True,
                        "synonyms": synonyms,
                    },
                },
                "analyzer": {
                    "polish_analyzer": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "polish_stop",
                        ],
                    },
                    "polish_with_synonyms": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "synonym_filter",
                            "polish_stop",
                        ],
                    },
                    "autocomplete_analyzer": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "edge_ngram_filter",
                        ],
                    },
                    "search_analyzer": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                        ],
                    },
                },
            },
        },
        "mappings": {
            "properties": {
                # === Product identity ===
                "id_verto": {"type": "keyword"},
                "id_internal": {"type": "keyword"},
                "ean": {"type": "keyword"},
                "link": {"type": "keyword", "index": False},
                "image": {"type": "keyword", "index": False},
                "condition": {"type": "keyword"},

                # === Name — primary search field ===
                "name": {
                    "type": "text",
                    "analyzer": "polish_with_synonyms",
                    "fields": {
                        "exact": {
                            "type": "text",
                            "analyzer": "standard",
                        },
                        "autocomplete": {
                            "type": "text",
                            "analyzer": "autocomplete_analyzer",
                            "search_analyzer": "search_analyzer",
                        },
                        "keyword": {
                            "type": "keyword",
                        },
                    },
                },

                # === Description — secondary search field ===
                "description": {
                    "type": "text",
                    "analyzer": "polish_analyzer",
                },

                # === Brand ===
                # fielddata=true enables aggregations / script access on the
                # analyzed text variant. Most code paths use brand.keyword,
                # but a few match clauses in search_engine.py touch the
                # analyzed `brand` field, which on Bonsai/OpenSearch fails
                # without fielddata enabled.
                "brand": {
                    "type": "text",
                    "analyzer": "standard",
                    "fielddata": True,
                    "fields": {
                        "keyword": {"type": "keyword"},
                    },
                },

                # === Category (hierarchical) ===
                "category": {
                    "type": "text",
                    "analyzer": "polish_analyzer",
                    "fields": {
                        "keyword": {"type": "keyword"},
                        "path": {
                            "type": "keyword",
                        },
                    },
                },
                "category_lvl0": {"type": "keyword"},
                "category_lvl1": {"type": "keyword"},
                "category_lvl2": {"type": "keyword"},

                # === Pricing ===
                "price": {"type": "float"},
                "sales_price": {"type": "float"},

                # === Availability ===
                "availability": {"type": "keyword"},

                # === Combined search field ===
                # Concatenation of name + brand + category for single-field
                # cross-term matching. Analyzed with synonyms.
                # UNIVERSAL: every product gets the same treatment.
                "searchable_text": {
                    "type": "text",
                    "analyzer": "polish_with_synonyms",
                },

                # === Google Analytics popularity signals ===
                "ga_views": {"type": "integer"},
                "ga_sales": {"type": "integer"},
                "popularity_score": {"type": "float"},
            }
        },
    }


# Backward compatibility: static reference for existing code
INDEX_SETTINGS = get_index_settings()
