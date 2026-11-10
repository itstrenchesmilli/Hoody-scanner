"""Crypto-only universe filter for Bitunix USDT perps.

Bitunix trading_pairs has no category field, so classification is:

1. A maintained allow-list for known crypto tickers that collide with
   stock/ETF names (STRK, COMP, STX, …).
2. A maintained exclusion list of tokenized stocks, pre-IPO names, ETFs,
   indices, commodities, and FX-style underlyings.
3. CoinGecko coins/list name+id patterns (tokenized stock, xStock, Dinari,
   Backpack Securities, Robinhood Token, PreStocks, pre-IPO, tokenized gold…).
4. Ticker/name heuristics for leftovers (suffix STOCK, leveraged ETF patterns,
   well-known company names not on CoinGecko as a genuine crypto).

New meme tickers that are not on CoinGecko and do not look like TradFi are kept.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

CACHE = Path(__file__).resolve().parents[1] / "data" / "cg_coins_cache.json"
CACHE_TTL = 12 * 3600

# Established cryptos whose ticker collides with a stock, ETF, or meme-of-the-stock.
CRYPTO_ALLOW = frozenset({
    "BTC", "ETH", "XRP", "BNB", "SOL", "ADA", "DOGE", "DASH", "TRX", "TON", "AVAX", "DOT",
    "LINK", "LTC", "BCH", "SUI", "NEAR", "APT", "ATOM", "SHIB", "PEPE", "UNI",
    "AAVE", "MKR", "CRV", "SNX", "LDO", "OP", "ARB", "IMX", "STX", "STRK", "INJ",
    "TIA", "SEI", "FET", "RENDER", "RNDR", "WLD", "JUP", "JTO", "PYTH", "WIF",
    "BONK", "FLOKI", "ORDI", "SATS", "RUNE", "GMX", "CFX", "IOTX", "COMP", "GAS",
    "BAND", "ONDO", "PENDLE", "ENA", "ETHFI", "HYPE", "TAO", "KAS", "FIL", "ICP",
    "XLM", "XMR", "ZEC", "ETC", "VET", "ALGO", "SAND", "MANA", "AXS", "APE",
    "BLUR", "MASK", "GRT", "ENS", "QNT", "ZEC", "B", "S", "T", "C", "OPEN",
    "TRUST", "RED", "SOON", "XAI", "RPL", "CYPH", "ARX", "RAM", "RE", "LITE",
    "BEAMX", "LUNA2", "LUNC", "LIGHTER", "PUMPFUN", "PUMP", "CROSS", "EDGEX",
    "AKE", "JCT", "IRYS", "INIT", "PONS", "BTR", "MAGMA", "LIGHT", "AVAAI",
    "IDOL", "ARIA", "KORU", "VVV", "XPL", "ON", "ASTER", "UAI", "PIEVERSE",
    "SKHY", "BTW", "CAP", "Q", "MON", "SOMI", "RIVER", "APR", "SAPIEN", "TAC",
    "RESOLV", "FIO", "AGT", "LAPTOP", "HAJIMI", "PHAROS", "MINIMAX", "MOONSHOT",
    "SPORTFUN", "KSTR", "LGE", "GIGADEV", "SHAZ", "LYTE", "MUU",
    "HIVE", "IO", "CFG", "CTR", "BOT", "CT", "BR", "FIO", "HIGH", "HIFI",
    "OM", "AGT", "LAPTOP", "MOTHER", "AI16Z", "PUMP", "1000BONK", "1000FLOKI",
    "1000MUMU", "1000CAT", "1000TURBO",
    "AIGENSYN", "GWEI", "BIRB", "ELSA", "BREV", "PRL", "GENIUS", "GRVT", "INFQ",
    "NIULAI", "BSB", "EWT", "ZBCN", "ZORA", "ZRO", "ZRX", "ZIL", "ZK", "ZKC",
    "ZEREBRO", "PENGU", "POPCAT", "MEW", "NOT", "TNSR", "CHILLGUY", "FARTCOIN",
    "BANANAS31", "USUAL", "MYX", "AERO", "ORDER", "ATH", "TA", "BIGTIME",
    "1000PEPE", "1000FLOKI", "1000BONK", "1000CAT", "1000TURBO", "TRUMP",
    "MELANIA", "VIRTUAL", "MORPHO", "SPX",  # SPX6900 meme, not the S&P index
    "HBAR", "CRO", "OKB", "W", "MOVE", "ME", "KAITO", "KAIA", "SAGA", "PIXEL",
    "PEOPLE", "CHZ", "OGN", "API3", "NMR", "1INCH", "OM", "AI16Z", "DOGS",
    "MOTHER", "HIFI", "HIGH", "RATS", "SUSHI", "TURBO", "JASMY", "MEME", "BOME",
    "PNUT", "GOAT", "GRASS", "MOODENG", "NEIRO", "ACT", "POL", "CATI", "HMSTR",
    "ENA", "WEN", "MOG", "SLERF", "MICHI", "GIGA", "ZETA", "ZK", "MANTA",
    "ALT", "DYM", "STRK", "BLAST", "MERL", "PORTAL", "ACE", "XAI", "HOOK",
})

# Tokenized stocks, pre-IPO, ETFs, indices, commodities, FX, company names.
TRADFI_BASES = frozenset({
    # US mega / magnificent / common equities
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "ORCL", "AMD", "INTC", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "MU", "ARM",
    "SMCI", "SNDK", "CRDO", "ALAB", "PLTR", "SNOW", "MDB", "CRM", "NOW", "ADBE",
    "PANW", "FTNT", "DDOG", "NET", "ZS", "CRWD", "GTLB", "PATH", "U", "IO",
    "IBM", "ACN", "CSCO", "ORCL", "SAP", "SHOP", "SQ", "COIN", "HOOD", "MSTR",
    "MARA", "RIOT", "HUT", "IREN", "CLSK", "HIVE",  # HIVE is also a crypto — see note below
    "BMNR", "NBIS", "OKLO", "IONQ", "QBTS", "RGTI", "QUBT",
    "NFLX", "DIS", "CMCSA", "T",  # T kept in CRYPTO_ALLOW as Threshold — allow wins
    "PARA", "WBD", "LYV",
    "JPM", "BAC", "GS", "MS", "C",  # C allow-listed as Chainbase/C
    "WFC", "USB", "SCHW", "BLK", "BX", "KKR", "APO", "ARES", "IBKR",
    "V", "MA", "PYPL", "PAYP", "AXP", "DFS",
    "UNH", "LLY", "JNJ", "PFE", "MRK", "ABBV", "ABT", "TMO", "DHR", "AMGN",
    "GILD", "BMY", "MRNA", "NVO", "AZN", "VRTX", "REGN", "ISRG",
    "WMT", "COST", "TGT", "HD", "LOW", "NKE", "MCD", "SBUX", "CMG", "ORLY",
    "KO", "PEP", "PG", "UL", "CL", "MDLZ", "GIS",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "MPC", "PSX", "EQT", "NATGAS",
    "BA", "CAT", "GE", "HON", "UNP", "UPS", "FDX", "DE", "LMT", "RTX", "NOC",
    "F", "GM", "RIVN", "LCID", "TSLA",
    "UBER", "ABNB", "LYFT", "DASH", "BKNG", "MELI", "PDD", "BABA", "JD", "BIDU",
    "NIO", "XPEV", "LI", "TSM", "ASML", "SONY", "NTES", "TCEHY", "TENCENT",
    "MEITUAN", "KUAISHOU", "XIAOMI", "BYD", "NIO", "LI", "JD",
    "SAMSUNG", "SAMSUNGEM", "HYUNDAI", "NAVER", "SKHYNIX", "SKH", "SKHY",  # SKHY maybe crypto — allow list wins if present
    "TM", "HMC", "SFTBY",
    "LLY", "NVO", "AZN",
    "LLY", "CRM", "NOW",
    "LLY",
    "DELL", "HPQ", "HPE", "CSCO", "ANET", "GLW", "KEYS", "TEL", "COHR", "CIEN",
    "AAOI", "APLD", "CRWV", "CRCL", "C3", "AI", "SOUN", "BBAI",
    "ASTS", "RKLB", "SPCE", "LUNR", "JOBY",
    "UBER", "DASH",
    "ABNB",
    "SBUX", "CMG",
    "MCD", "YUM",
    "NKE", "LULU", "ONON",
    "EL", "PG",
    "PM", "MO", "BTI",
    "KO", "PEP",
    "COST", "WMT",
    "TGT", "DG",
    "HD", "LOW",
    "TSCO",
    "CVNA", "AN", "KMX", "RIVN",
    "FUTU", "TIGR", "IBKR", "HOOD", "COIN", "NMR",  # NMR is also Numeraire — allow wins
    "SOFI", "AFRM", "UPST", "LC", "NU", "PAGS", "STNE",
    "SE", "GRAB", "CPNG",
    "SNAP", "PINS", "RDDT", "MTCH", "BMBL",
    "TTWO", "EA", "RBLX", "U", "NTDOY",
    "NKE",
    "SPOT", "SONO",
    "ZM", "DOCU", "OKTA", "TWLO", "ESTC", "HUBS", "WDAY", "INTU", "ADSK",
    "CDNS", "SNPS", "ANSS",
    "KLAC", "LRCX", "AMAT", "TER",
    "TXN", "ADI", "NXPI", "MCHP", "ON",  # ON may be a crypto — allow wins
    "MRVL", "AVGO", "QCOM", "SWKS", "QRVO",
    "ARM", "TSM", "ASML", "AMAT",
    "SMCI", "DELL", "HPQ", "NTAP", "WDC", "STX",  # STX allow-listed Stacks
    "PSTG",
    "ORCL", "MSFT", "AMZN", "GOOGL", "META",
    "NFLX", "DIS",
    "CMCSA",
    "CHTR",
    "PARA",
    "WBD",
    "FOX", "FOXA",
    "NYT",
    "ROKU",
    "TTD", "ZG", "Z",
    "OPEN",  # allow-listed OpenLedger
    "COMP",  # allow-listed Compound
    "OPENAI", "ANTHROPIC", "SPCX", "SPACE", "SPACE-X", "SPACEX",
    "SOFTBANK", "TESCREEN",
     # consumer robotics listing on Bitunix — treat as equity-style unless allow
    "POPMART", "SHEIN", "TEMU",
    "GAMESTOP", "AMC", "BB", "BBBY", "KOSS", "GME",
    "BRKB", "BRK.B", "BRKA",
    "BERKSHIRE",
    "DJT", "TRUTH",  # DJT is Trump media stock; TRUTH may be a crypto — not in allow, exclude DJT only
    "EBAY", "ETSY", "W",  # W allow-listed if in CRYPTO_ALLOW (Wormhole / rare)
    "AKAM", "AKAMAI",
    "NOK", "ERIC",
    "STM", "NXPI",
    "AMGN", "GILD",
    "VKTX", "MDGL", "GPCR",
    "HIMS", "SMCI",
    "OURA", "WHOOP",
    "CRML", "MP", "LAC", "ALB", "SQM", "FCX", "NEM", "GOLD",  # GOLD often a meme — careful, not all gold tickers
    "AEM", "WPM", "AG", "HL", "CDE",
    "USAR", "URNM", "CCJ", "UEC", "SMR", "OKLO", "VST", "CEG", "TLN", "GEV",
    "FLNC", "ENPH", "SEDG", "FSLR", "RUN", "ARRY",
    "PLUG", "BE", "BLDP",
    "TSLA",
    "RIVN",
    "LCID",
    "NIO",
    "XPEV",
    "LI",
    "CSOPSX2L", "CSOPHLS2L",
    "KODEX200",
    "EIDO", "EWJ", "EWY", "EWZ", "EWZS", "EEM", "VWO", "IWM", "IWN", "IWF",
    "SPY", "SPX",  # SPX is allow-listed as SPX6900
    "VOO", "IVV", "QQQ", "QQQM", "DIA", "TQQQ", "SQQQ", "QLD", "QID",
    "SOXL", "SOXS", "TECL", "TECS", "TNA", "TZA", "UPRO", "SPXU", "SPXL", "SPXS",
    "UVXY", "VXX", "VIXY", "SVXY", "UVIX",
    "TBT", "TMF", "TQQQ", "SQQQ", "SOXL", "SOXS", "MSTU", "MSTX", "TSLL", "NVDL",
    "CONL", "AMDL", "NVDX", "NVDQ", "TSLG",
    "BITO", "BITX", "ETHU", "SATO",
    "GDX", "GDXJ", "SLV", "GLD", "IAU", "USO", "UNG", "DBA", "DBC", "PDBC",
    "XLE", "XLF", "XLI", "XLK", "XLV", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    "XBI", "SMH", "SOXX", "HACK", "BOTZ", "ARKK", "ARKQ", "ARKW", "ARKG", "ARKF",
    "KWEB", "CQQQ", "FXI", "MCHI", "ASHR", "YINN", "YANG",
    "SHLD",  # Global X Defense ETF / old Sears ticker
    "SNXX", "STXX", "MDTUS",
    "QNTX",
    # Commodities / metals / energy underlyings (including on-chain gold tokens)
    "XAU", "XAG", "XPD", "XPT", "XCU", "COPPER", "NATGAS", "NG", "WTI", "BRENT",
    "OIL", "BRENT", "CLUS", "BZ",  # BZ can be a crypto — listed as oil often; exclude (not in allow)
    "PAXG", "XAUT", "XAUt", "IAU", "GLDM",
    "SILVER", "PLATINUM", "PALLADIUM",
    "BWET",  # wet freight / commodity style
    # FX / dollar index style
    "DXY", "USDX", "USDEX", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF",
    "EURUSD", "GBPUSD", "USDJPY", "DX",
    # Misc tokenized / pre-IPO / company
    "CATERPILLAR", "CAT",  # CAT is Caterpillar here more than a meme
    "PENGSTOCK", "4STOCK",
    "HANMI", "ZHONGJI", "ZHIPU", "CXMT", "AGPU", "AXTI", "INTW", "TWST",
    "ZCSH", "NIULAI", "GIGADEV",  # GIGADEV in allow as possible crypto
    "PAYP", "COST", "ABBV", "LLY", "NVO", "ORCL", "CSCO", "QCOM", "ARM",
    "PDD", "BABA", "JD", "BIDU", "NKE", "DIS", "KO", "WMT", "MCD", "PFE",
    "XOM", "CVX", "OXY", "IBM", "HD", "GS", "JPM", "BAC", "V", "MA",
    "DELL", "AMD", "INTC", "MU", "MRVL", "AMAT", "ASML", "TSM", "SONY",
    "NFLX", "SHOP", "SNOW", "PLTR", "UBER", "ABNB", "MELI", "SE",
    "RIVN", "SOFI", "HOOD", "COIN", "MSTR", "MSTU", "TSLA", "TSLL", "NVDA", "NVDL",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NFLX",
    "RDDT", "DJT", "FUTU", "IBKR", "HIMS", "SMCI", "APP", "ARM", "CRWD",
    "NET", "DDOG", "ZS", "OKTA", "SNOW", "MDB", "ESTC",
    "TTD", "ROKU", "SNAP", "PINS", "LYFT",
    "BA", "GE", "CAT", "HON", "MMM", "UNP",
    "LMT", "RTX", "NOC", "GD",
    "PM", "MO", "RAI",
    "PEP", "KO", "MNST",
    "SBUX", "CMG", "YUM", "QSR", "DPZ", "WING",
    "NKE", "ADDYY", "LULU",
    "EL", "UL", "CL", "KMB", "GIS", "K", "CPB", "CAG", "SJM", "HSY", "MDLZ",
    "TMO", "DHR", "SYK", "BSX", "EW", "IDXX", "A", "IQV", "IQ",  # A / IQ ambiguous
    "UNH", "ELV", "CI", "HUM", "CNC", "MOH",
    "CVS", "WBA", "RAD",
    "SPGI", "MCO", "VRSK", "EFX",
    "ADP", "PAYX", "PAYC", "PCTY",
    "FI", "FIS", "FISV", "GPN", "JKHY",
    "BR", "CTSH", "IT", "ACN", "IBM", "INFY", "WIT",
    "TEAM", "WDAY", "NOW", "HUBS", "VEEV", "BILL", "ZI", "PCTY",
    "ADSK", "ANSS", "CDNS", "SNPS", "PTC",
    "KLAC", "LRCX", "AMAT", "ASML", "TER", "ENTG", "UCTT", "ONTO", "CAMT",
    "MCHP", "NXPI", "ADI", "TXN", "ON", "SWKS", "QRVO", "MPWR", "RMBS", "ALGM",
    "AVGO", "QCOM", "MRVL", "AMD", "NVDA", "INTC", "ARM", "TSM", "MU", "WDC", "STX",
    "SMCI", "DELL", "HPQ", "HPE", "NTAP", "PSTG", "ANET", "CSCO", "JNPR", "FFIV", "CIEN", "COHR", "LITE",
    "OKLO", "SMR", "CEG", "VST", "TLN", "VRT", "PWR", "ETN", "EMR", "ROK", "AME",
    "GEV", "GE", "PH", "ITW", "DOV", "XYL", "IEX", "FTV", "TDY", "KEYS", "FTI",
    "FCX", "SCCO", "NEM", "GOLD", "AEM", "WPM", "FNV", "RGLD", "AG", "HL", "CDE", "PAAS", "SSRM",
    "ALB", "SQM", "LAC", "LTHM", "PLL", "SGML", "MP", "UUUU", "CCJ", "NXE", "UEC", "DNN", "URG", "URNM",
    "VALE", "BHP", "RIO", "GLEN",
    "X", "CLF", "NUE", "STLD", "RS", "CMC", "VRT",
    "AA", "CENX",
    "DOW", "LYB", "EMN", "APD", "LIN", "SHW", "PPG", "ECL", "IFF", "ALB",
    "ADM", "BG", "CTVA", "MOS", "NTR", "CF",
    "DE", "CNHI", "AGCO", "CUB",
    "CAT", "PCAR", "CMI", "IR", "PH",
    "UNP", "CSX", "NSC", "CP", "CNI", "ODFL", "XPO", "CHRW", "EXPD", "FDX", "UPS", "DAL", "UAL", "AAL", "LUV", "ALK", "JBLU",
    "WM", "RSG", "WCN", "SRCL",
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "PEG", "ED", "EIX", "XEL", "WEC", "ES", "AWK", "AEE", "DTE", "PPL", "FE", "ETR", "CEG", "VST", "PCG", "EIX",
    "AMT", "CCI", "SBAC", "EQIX", "DLR", "PLD", "PSA", "SPG", "O", "VICI", "WELL", "AVB", "EQR", "ESS", "MAA", "UDR", "CPT", "AIV", "BXP", "VTR", "PEAK", "HST", "MAC", "KIM", "REG", "FRT", "SLG", "CUZ", "HIW", "DEI", "JBGS", "PGRE", "BDN", "OFC", "ARE", "KRC",
    "O",  # Realty Income — may collide; not in crypto allow
    "SPG",
    "PLD",
    "CCI", "AMT",
    "EQIX", "DLR",
    "WELL", "VTR", "PEAK", "OHI",
    "HST", "HLT", "MAR", "H", "WH", "CHH", "IHG",
    "BKNG", "EXPE", "ABNB", "RCL", "CCL", "NCLH", "CUK",
    "YUM", "QSR", "DPZ", "WING", "SBUX", "MCD", "CMG", "SHAK",
    "NKE", "SKU", "DECK", "CROX", "BIRK", "ONON",
    "TPR", "CPRI", "RL", "PVH", "VFC", "COLM", "HES",
    "EL", "UL", "CL", "PG", "KMB", "CHD", "CLX",
    "COST", "WMT", "TGT", "DG", "DLTR", "BJ", "KR", "CASY",
    "TJX", "ROST", "BURL", "ANF", "AEO", "GPS", "LB", "URBN",
    "AZO", "ORLY", "AAP", "GPC",
    "BBY", "WSM", "RH", "W",
    "TSCO", "POOL", "HD", "LOW", "FND",
    # Korean / China / EU names seen on Bitunix
    "SAMSUNG", "SAMSUNGEM", "HYUNDAI", "NAVER", "SKHYNIX", "KODEX200",
    "TENCENT", "MEITUAN", "KUAISHOU", "XIAOMI", "BYD", "BABA", "PDD", "JD",
    "SHEIN", "POPMART", "HANMI", "ZHONGJI", "ZHIPU",
    "ASML", "SAP", "NOK", "ERIC", "STM", "SIE", "AIR", "MC",
    # Extra tickers observed on Bitunix USDT book
    "AAOI", "ABBV", "ACN", "ADBE", "AIGENSYN", "AKAM", "ALAB", "AMAT", "AMC",
    "AMGN", "AMZN", "ANET", "APLD", "APP", "ARM", "ASML", "ASTS", "AVGO", "AXTI",
    "BABA", "BAC", "BE", "BILL", "BITO", "BMNR", "BRKB", "BWET", "BX", "BZ",
    "CATERPILLAR", "CBRS", "CEG", "CFG", "CIEN", "COHR", "COIN", "COST", "CP",
    "CRCL", "CRDO", "CRM", "CRML", "CRWV", "CSCO", "CSOPHLS2L", "CSOPSX2L",
    "CT", "CTR", "CVNA", "CVX", "CXMT", "DDOG", "DELL", "DIA", "DIS", "DJT",
    "DKNG", "EBAY", "EIDO", "EQT", "EWJ", "EWY", "EWZ", "EWZS", "F", "FIG",
    "FLNC", "FUTU", "GDX", "GEV", "GLW", "GOOGL", "GPRO", "GS", "GTLB",
    "HD", "HIMS", "HOOD", "HPE", "HUT", "HYUNDAI", "IBKR", "IBM", "INTC",
    "INTW", "IONQ", "IREN", "IWM", "JPM", "KLAC", "KO", "KODEX200", "KUAISHOU",
    "LLY", "LRCX", "MARA", "MCD", "MDB", "MDTUS", "MEITUAN", "MELI", "META",
    "MP", "MRK", "MRNA", "MRVL", "MSFT", "MSTR", "MSTU", "MU", "NAVER", "NBIS",
    "NFLX", "NKE", "NOK", "NVO", "NVDA", "NVDL", "OKLO", "ONDS", "ORCL", "OURA",
    "OXY", "PANW", "PAYP", "PDD", "PEP", "PFE", "PLTR", "POPMART", "PYPL",
    "QBTS", "QCOM", "QNTX", "QQQ", "RDDT", "RDW", "RIVN", "RKLB", "SAMSUNG",
    "SAMSUNGEM", "SHEIN", "SHLD", "SHOP", "SKDD", "SKHYNIX", "SLX", "SMCI",
    "SMH", "SMR", "SNDK", "SNOW", "SNPS", "SNXX", "SOFI", "SONY", "SOXL",
    "SOXS", "SPCX", "SPY", "SQQQ", "STM", "STRC", "STXX", "TBT", "TEAM",
    "TEM", "TENCENT", "TMF", "TQQQ", "TSLA", "TSLL", "TSM", "TTWO", "TWST",
    "TXN", "TZA", "UBER", "UNH", "URNM", "USAR", "UVXY", "V", "VKTX", "VRT",
    "VST", "WDC", "WMT", "XAG", "XAU", "XAUT", "XBI", "XIAOMI", "XLE", "XOM",
    "XPD", "XPT", "ZS", "ZM", "PAXG", "NATGAS", "COPPER", "OPENAI", "ANTHROPIC",
    "PENGSTOCK", "4STOCK", "GAMESTOP", "ZHIPU", "ZHONGJI", "HANMI",
    "ZCSH", "USDEX", "AGPU", "NIULAI", "UNITREE",
})

# Company / product names that show up as bases even when not a US ticker.
TRADFI_NAME_BASES = frozenset({
    "SAMSUNG", "SAMSUNGEM", "HYUNDAI", "TENCENT", "MEITUAN", "KUAISHOU",
    "XIAOMI", "NAVER", "SKHYNIX", "CATERPILLAR", "GAMESTOP", "SHEIN",
    "POPMART", "OPENAI", "ANTHROPIC", "ZHIPU", "ZHONGJI", "HANMI",
})

TOKENIZED_RE = re.compile(
    r"tokenized|tokenised|xstock|\bxstocks\b|bstock|prestocks|pre-ipo|preipo|"
    r"backpack securities|robinhood token|dinari|ondo tokenized|"
    r"nasdaq\s*:|nyse\s*:|listed share|equity perp|"
    r"tokenized[ -](stock|etf|etp|commodity|gold|silver|pre-ipo|treasury)|"
    r"•\s*robinhood|robinhood chain|four\.?meme tokenized|"
    r"pax gold|tether gold|perth mint gold",
    re.I,
)

ETF_OR_LEV_RE = re.compile(
    r"^(SPY|QQQ|QQQM|DIA|IWM|IWF|IWD|IWN|VTI|VOO|IVV|TQQQ|SQQQ|QLD|QID|"
    r"SOXL|SOXS|TECL|TECS|TNA|TZA|UPRO|SPXU|SPXL|SPXS|UVXY|VXX|SVXY|"
    r"TBT|TMF|BITO|BITX|NVDL|TSLL|MSTU|MSTX|CONL|AMDL|"
    r"GDX|GDXJ|SLV|GLD|IAU|USO|UNG|XLE|XLF|XLK|XLV|XBI|SMH|SOXX|"
    r"ARKK|ARKQ|ARKW|KWEB|YINN|YANG|KODEX|CSOP)",
    re.I,
)
STOCK_SUFFIX_RE = re.compile(r"(STOCK|ETF|ETP|PREIPO|PRE-IPO)$", re.I)
LEV_PRODUCT_RE = re.compile(r"(2L|3L|5L|2S|3S|5S)$")


def _is_tokenized_hit(name: str, cid: str) -> bool:
    blob = f"{name} {cid}"
    return bool(TOKENIZED_RE.search(blob))


def load_cached_coins() -> list[dict]:
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return []
    return []


def save_cached_coins(coins: list[dict]) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(coins))
    except Exception:
        pass


async def fetch_coingecko_coins(http: httpx.AsyncClient | None = None) -> list[dict]:
    cached = load_cached_coins()
    if cached:
        return cached
    close = False
    if http is None:
        http = httpx.AsyncClient(timeout=45.0, headers={"User-Agent": "HoodySetupScanner/1.0"})
        close = True
    try:
        r = await http.get("https://api.coingecko.com/api/v3/coins/list")
        if r.status_code == 200 and isinstance(r.json(), list):
            coins = r.json()
            save_cached_coins(coins)
            return coins
    except Exception:
        pass
    finally:
        if close:
            await http.aclose()
    return cached


def classify_base(base: str, cg_by_sym: dict[str, list[dict]] | None = None) -> tuple[bool, str]:
    """Return (is_crypto, reason). Allow-list always wins."""
    b = (base or "").upper().strip()
    if not b:
        return False, "empty-base"
    if b in CRYPTO_ALLOW:
        return True, "crypto-allowlist"
    if b in TRADFI_BASES or b in TRADFI_NAME_BASES:
        return False, "exclusion-list"
    if STOCK_SUFFIX_RE.search(b) or b.endswith("STOCK"):
        return False, "name-heuristic:stock-suffix"
    if ETF_OR_LEV_RE.match(b) or LEV_PRODUCT_RE.search(b) and b.startswith("CSOP"):
        return False, "name-heuristic:etf-or-leveraged"
    if b in {"XAU", "XAG", "XPD", "XPT", "PAXG", "XAUT", "COPPER", "NATGAS", "DXY", "USDEX", "USDX"}:
        return False, "commodity-or-fx"

    hits = (cg_by_sym or {}).get(b) or []
    crypto_hits = []
    tok_hits = []
    for h in hits:
        if _is_tokenized_hit(h.get("name") or "", h.get("id") or ""):
            tok_hits.append(h)
        else:
            crypto_hits.append(h)

    if tok_hits and not crypto_hits:
        return False, "coingecko:tokenized-only"
    if tok_hits and crypto_hits:
        # Ticker collision: if the only "crypto" looks like a stock-meme wrapper
        # (TSLA6900, MSTR2100, Backpack Securities already tokenized), treat as TradFi.
        genuine = []
        for h in crypto_hits:
            cid = (h.get("id") or "").lower()
            name = (h.get("name") or "").lower()
            if "backpack securities" in name or "robinhood" in name or "dinari" in name:
                continue
            if re.search(rf"^{re.escape(b.lower())}[-_]?(6900|2100|4663)", cid):
                continue
            genuine.append(h)
        if not genuine:
            return False, "coingecko:stock-or-stock-meme"
        # Ambiguous (e.g. META = Metadium vs Meta stock). Default exclude for
        # 1–5 letter traditional tickers when a tokenized stock also exists,
        # unless already allow-listed above.
        if len(b) <= 5 and b.isalpha():
            return False, "coingecko:ticker-collision-tradfi-default"
        return True, "coingecko:genuine-crypto-collision"
    if crypto_hits:
        return True, "coingecko:crypto"
    # Not on CoinGecko: keep new memes; drop obvious company-style names.
    if len(b) <= 5 and b.isalpha() and b in TRADFI_BASES:
        return False, "exclusion-list"
    return True, "kept-unlisted-alt"


def classify_symbols(
    symbols: list[str],
    cg_coins: list[dict] | None = None,
) -> dict[str, Any]:
    cg_by_sym: dict[str, list[dict]] = {}
    for c in cg_coins or []:
        sym = (c.get("symbol") or "").upper()
        if sym:
            cg_by_sym.setdefault(sym, []).append(c)
    kept = []
    excluded = []
    for sym in symbols:
        base = sym.upper().replace("USDT", "").replace("USDC", "").replace("USD", "") if isinstance(sym, str) else ""
        # safer base split
        s = (sym or "").upper()
        if s.endswith("USDT"):
            base = s[:-4]
        elif s.endswith("USDC"):
            base = s[:-4]
        else:
            base = s
        ok, reason = classify_base(base, cg_by_sym)
        if ok:
            kept.append(s)
        else:
            excluded.append({"symbol": s, "base": base, "reason": reason})
    return {"kept": kept, "excluded": excluded}


def is_mega_major(base: str) -> bool:
    return base.upper() in {"BTC", "ETH", "XRP", "BNB", "SOL"}


def is_context_major(base: str) -> bool:
    return base.upper() in {
        "BTC", "ETH", "XRP", "BNB", "SOL", "ADA", "DOGE", "TRX", "TON", "LTC",
        "BCH", "DOT", "AVAX", "LINK", "SUI", "SHIB", "PEPE", "1000PEPE",
    }
