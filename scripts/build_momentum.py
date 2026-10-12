#!/usr/bin/env python3
"""Rebuild data/momentum_winners.csv, data/momentum_top5.csv and data/momentum.json for momentum.html.

Winners are the top calendar-year PRICE return among stocks that were S&P 500 members at the
END of that year (the convention used by S&P Dow Jones Indices and year-end press lists).
Winners and returns for live tickers are recomputed from Yahoo Finance (yfinance); for
delisted/acquired names the values are hard-coded below with their sources.
Strategy returns use TOTAL return (Yahoo 'Adj Close', or Exa total-return index for delisted names).
Benchmark: S&P 500 Total Return index (^SP500TR, Yahoo).
Requires: pip install yfinance pandas.   Run: python3 scripts/build_momentum.py
"""
import csv, json, datetime
from pathlib import Path
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
SPDJI = "https://www.visualcapitalist.com/top-sp-500-stocks-by-annual-returns/"
def exa(t, y): return f"https://exa.ai/library/markets/stock/{t}?date={y}-12-31&view=return&from={y}-01-01&basis=total_return"

# year: (ticker, company, yahoo_symbol or None, status, sources, note)
W = {
 1996: (None, None, None, "unverified", [], "No reliable year-end 1996 list found. StatMuse (current-member screen) names Jabil, which was not an S&P 500 member in 1996; Yahoo has no data for most 1996 members (many delisted, tickers reused). Left blank on purpose."),
 1997: ("DELL", "Dell Computer", None, "single-source", [SPDJI], "S&P DJI top-50 list: +216% (price). Old Dell (taken private 2013) not on Yahoo; no second source found."),
 1998: ("DELL", "Dell Computer", None, "single-source", [SPDJI], "S&P DJI top-50 list: +248% (price; no dividends, so = total return)."),
 1999: ("QCOM", "Qualcomm", "QCOM", "verified", [SPDJI, "yahoo"], ""),
 2000: ("RBK", "Reebok International", None, "verified", ["https://nypost.com/2000/12/31/some-stocks-didnt-crash-they-shone/", exa("RBK", 2000), SPDJI], "NY Post (Dec 31 2000) and Exa total return both give Reebok +233.9% and Dynegy +218% to +224%. The S&P DJI top-50 list shows Dynegy at +361%, which looks like a pre-merger (Illinova, Feb 2000) price basis, so it was not used. Reebok was acquired by adidas in Jan 2006, after the holding window."),
 2001: ("NVDA", "NVIDIA", "NVDA", "verified", [SPDJI, "yahoo"], "Joined the index Nov 2001 (year-end member)."),
 2002: ("PVN", "Providian Financial", None, "verified", ["https://www.iese.edu/media/research/pdfs/DI-0501-E.pdf", exa("PVN", 2002)], "Acquired by Washington Mutual Oct 2005 (after the holding window)."),
 2003: ("AV", "Avaya", None, "verified", [SPDJI, exa("AV", 2003)], "Taken private Oct 2007 (after the holding window)."),
 2004: ("ADSK", "Autodesk", "ADSK", "probable", ["yahoo", "https://www.statmuse.com/money/ask/best-performing-tech-stocks-in-the-s-and-p-500-in-the-year-2004"], "Yahoo coverage of 2004 members is incomplete (delisted names missing); no contemporaneous full-index list found."),
 2005: ("VLO", "Valero Energy", "VLO", "verified", ["yahoo", "https://money.cnn.com/2005/12/21/markets/sp_winners/"], ""),
 2006: ("ATI", "Allegheny Technologies", "ATI", "probable", ["yahoo", "https://www.statmuse.com/money/ask/20-best-performing-stocks-in-the-s-and-p-2006"], "StatMuse ranks ICE first, but ICE joined the S&P 500 only in 2007; ATI is next (+151%). Yahoo coverage of 2006 members incomplete."),
 2007: ("NOV", "National Oilwell Varco", "NOV", "verified", ["yahoo", "https://www.reuters.com/article/business/factbox-sp-500s-best-and-worst-performers-for-2007-idUSN31600240/"], ""),
 2008: ("FDO", "Family Dollar Stores", None, "verified", ["https://www.cnbc.com/2008/12/31/best-stock-of-2008.html", exa("FDO", 2008)], "Acquired by Dollar Tree Jul 2015 (after the holding window)."),
 2009: ("XL", "XL Capital", None, "verified", [SPDJI, exa("XL", 2009)], "Acquired by AXA 2018 (after the holding window)."),
 2010: ("NFLX", "Netflix", "NFLX", "verified", [SPDJI, "yahoo"], ""),
 2011: ("COG", "Cabot Oil & Gas", None, "verified", ["https://www.cnbc.com/2011/12/28/The-Best-Investments-of-2011.html", "https://www.thetimes-tribune.com/2012/01/07/cabot-tops-sp-500/", exa("COG", 2011)], "Now Coterra (CTRA); no longer on Yahoo, Exa total-return index used."),
 2012: ("PHM", "PulteGroup", "PHM", "verified", ["yahoo", "https://www.thefiscaltimes.com/2013/01/01/2012-Stock-Market-Winners-and-Losers"], ""),
 2013: ("NFLX", "Netflix", "NFLX", "verified", [SPDJI, "yahoo"], ""),
 2014: ("LUV", "Southwest Airlines", "LUV", "verified", ["yahoo", "press"], ""),
 2015: ("NFLX", "Netflix", "NFLX", "verified", ["yahoo", "press"], ""),
 2016: ("NVDA", "NVIDIA", "NVDA", "verified", [SPDJI, "yahoo"], ""),
 2017: ("NRG", "NRG Energy", "NRG", "verified", ["yahoo", "https://talkmarkets.com/content/stocks--equities/best-and-worst-performing-sp-500-stocks-in-2017?post=161048"], "Align Technology (+131%, joined Jun 2017) was a close second."),
 2018: ("AMD", "Advanced Micro Devices", "AMD", "verified", ["yahoo", "press"], ""),
 2019: ("AMD", "Advanced Micro Devices", "AMD", "verified", ["yahoo", "press"], ""),
 2020: ("TSLA", "Tesla", "TSLA", "verified", [SPDJI, "yahoo"], "Joined the index Dec 21, 2020 (year-end member)."),
 2021: ("DVN", "Devon Energy", "DVN", "verified", ["yahoo", "press"], ""),
 2022: ("OXY", "Occidental Petroleum", "OXY", "verified", ["yahoo", "press"], ""),
 2023: ("NVDA", "NVIDIA", "NVDA", "verified", ["yahoo", "press"], ""),
 2024: ("PLTR", "Palantir", "PLTR", "verified", ["yahoo", "https://www.fool.com/investing/2024/11/23/the-top-sp-500-stock-of-2024-so-far-isnt-nvidia-he/"], "Joined the index Sep 2024 (year-end member). Using Jan-1 members instead would give Vistra/NVIDIA."),
 2025: ("WDC", "Western Digital", "WDC", "verified", ["yahoo", "https://www.barrons.com/articles/western-digital-stock-best-sp500-2025-trade-desk-worst-f1bfdea5", "https://rubiewealth.com/sp-500-top-25-annual-performers-2025/"], "SanDisk (spun off Feb 2025, added Nov 2025) rose more from its debut but has no full-year 2025 return."),
 2026: ("MRNA", "Moderna", "MRNA", "ytd", ["yahoo", "https://topstocksytd.com/best-stocks-2026-so-far/"], "Year to date only. The +663% claim checks out against Yahoo closes (Dec 31, 2025 to the latest close)."),
}
# Hard-coded returns for names with no Yahoo history. price = winner-year price return; tr = total return by year.
HARD_PRICE = {1997: 2.16, 1998: 2.48, 2000: 2.3390, 2002: 0.8282, 2003: 4.2816, 2008: 0.3863, 2009: 3.95, 2011: 1.0096}
HARD_PRICE_SRC = {2000: "NY Post / Exa total return (233.9%)", 2002: "Exa total return (no dividends paid)", 2003: "Exa total return (no dividends paid)", 2008: "Exa total return", 2011: "Exa total return"}
HARD_TR = {  # (ticker, year): total return, source
 ("DELL", 1998): (2.48, SPDJI), ("DELL", 1999): (0.3937, exa("DELL", 1999)), ("DELL", 2000): (-0.6581, exa("DELL", 2000)),
 ("RBK", 2001): (-0.0307, exa("RBK", 2001)), ("RBK", 2002): (0.1094, exa("RBK", 2002)),
 ("PVN", 2003): (0.7935, exa("PVN", 2003)), ("PVN", 2004): (0.4149, exa("PVN", 2004)),
 ("AV", 2004): (0.3292, exa("AV", 2004)), ("AV", 2005): (-0.3797, exa("AV", 2005)),
 ("FDO", 2009): (0.0874, exa("FDO", 2009)), ("FDO", 2010): (0.8135, exa("FDO", 2010)),
 ("XL", 2010): (0.2155, exa("XL", 2010)), ("XL", 2011): (-0.0743, exa("XL", 2011)),
 ("COG", 2012): (0.3133, exa("COG", 2012)), ("COG", 2013): (0.5613, exa("COG", 2013)),
}

# ---------------- Strategy B: top 5 by year ----------------
FOOL08 = "https://www.fool.com/investing/general/2009/01/02/a-wonderful-year-for-these-stocks.aspx"
IBT10 = "https://www.ibtimes.com/ten-best-performing-stocks-sp-500-2010-252407"
CNBC11 = "https://www.cnbc.com/2011/12/28/The-Best-Investments-of-2011.html"
FT12 = "https://www.thefiscaltimes.com/2013/01/01/2012-Stock-Market-Winners-and-Losers"
FT13 = "https://www.thefiscaltimes.com/Articles/2013/12/31/You-Won-t-Believe-How-Much-Richer-Stock-Investors-Got-2013"
CNBC14 = "https://www.cnbc.com/2014/12/31/the-best-and-worst-stocks-of-2014.html"
FOOL15 = "https://www.fool.com/investing/general/2016/01/14/the-10-best-stocks-in-the-sp-500-in-2015.aspx"
FOOL16 = "https://www.fool.com/investing/2017/01/09/the-10-best-stocks-in-the-sp-500-in-2016.aspx"
FOOL17 = "https://www.fool.com/investing/2017/12/28/the-25-top-performing-sp-500-stocks-of-2017.aspx"
FOOL18 = "https://www.fool.com/investing/2019/01/18/the-5-top-performing-sp-500-stocks-of-2018.aspx"
MW19 = "https://www.marketwatch.com/story/here-are-the-best-and-worst-dow-and-sp-500-stocks-of-2019-2019-12-30"
CNBC20 = "https://www.cnbc.com/2020/12/31/here-are-the-top-performing-stocks-in-the-sp-500-for-2020.html"
CNBC21 = "https://www.cnbc.com/2022/01/13/op-ed-these-big-names-propelled-the-sp-500-in-2021-heres-whats-ahead-for-2022.html"
TL22 = "https://www.tastylive.com/news-insights/best-worst-sp500-sectors-2022"
TM22 = "https://talkmarkets.com/article/best-etfs-funds-and-stocks-of-2022-a-preview-to-2023"
MW23 = "https://www.marketwatch.com/story/these-20-stocks-soared-the-most-in-2023-e1488779"
YC24 = "https://get.ycharts.com/resources/blog/the-best-performing-stocks-of-2024/"
RW25 = "https://rubiewealth.com/sp-500-top-25-annual-performers-2025/"
NYP00 = "https://nypost.com/2000/12/31/some-stocks-didnt-crash-they-shone/"
# names: (ticker, company, yahoo symbol or None, hard-coded price return or None)
TOP5 = {
 1997: dict(status="unverified", names=[("DELL","Dell Computer",None,2.16)], sources=[SPDJI], note="Only the #1 is documented (S&P DJI list includes stocks of 212% or more). No full 1997 top-5 list found."),
 1998: dict(status="unverified", names=[("DELL","Dell Computer",None,2.48)], sources=[SPDJI], note="Only the #1 is documented. No full 1998 top-5 list found."),
 1999: dict(status="verified-list", names=[("QCOM","Qualcomm","QCOM",None),("S/PCS","Sprint (PCS tracking stock per S&P DJI)",None,3.43),("NXTL","Nextel Communications",None,3.36),("LSI","LSI Logic",None,3.19),("NT","Nortel Networks",None,3.04)], sources=[SPDJI], note="Top 5 from S&P DJI's list, which includes each year's top 10 (all 10 of 1999's were above 212%). Not used in Strategy B: the 1998 and 2000 lists are incomplete."),
 2000: dict(status="partial", names=[("RBK","Reebok International",None,2.3392),("DYN","Dynegy",None,2.1822),("EOG","EOG Resources","EOG",None)], sources=[NYP00], note="Only #1 to #3 are ranked in the source. Power-One, Coastal and Alza were also in the top 10, but their order is not given."),
 2001: dict(status="unverified", names=[("NVDA","NVIDIA","NVDA",None)], sources=[SPDJI], note="Only the #1 is documented."),
 2002: dict(status="unverified", names=[("PVN","Providian Financial",None,0.8282)], sources=["https://www.iese.edu/media/research/pdfs/DI-0501-E.pdf"], note="Only #1 is reliably documented."),
 2003: dict(status="verified-list", names=[("AV","Avaya",None,4.28),("WMB","Williams Cos.","WMB",None),("DYN","Dynegy",None,2.63),("PMCS","PMC-Sierra",None,2.62),("NOVL","Novell",None,2.15)], sources=[SPDJI], note="S&P DJI list (7 names of 212% or more in 2003; Novell and Corning were both about +215%, and Novell ranks higher on the list). Not used in Strategy B: 2002 and 2004 are incomplete."),
 2004: dict(status="unverified", names=[("ADSK","Autodesk","ADSK",None)], sources=[], note="No full top-5 list found."),
 2005: dict(status="unverified", names=[("VLO","Valero Energy","VLO",None)], sources=[], note="No full top-5 list found."),
 2006: dict(status="unverified", names=[("ATI","Allegheny Technologies","ATI",None)], sources=[], note="No full top-5 list found."),
 2007: dict(status="unverified", names=[("NOV","National Oilwell Varco","NOV",None),("AMZN","Amazon.com","AMZN",None),("JEC","Jacobs Engineering",None,1.3451)], sources=["https://www.reuters.com/article/business/factbox-sp-500s-best-and-worst-performers-for-2007-idUSN31600240/"], note="Only the top 3 could be confirmed (the Reuters factbox is paywalled; the search snippet gives #1 to #3)."),
 2008: dict(status="probable", names=[("FDO","Family Dollar Stores",None,0.3863),("UST","UST Inc.",None,None),("AMGN","Amgen","AMGN",None),("HRB","H&R Block","HRB",None),("CELG","Celgene",None,0.196)], sources=[FOOL08], note="The Motley Fool list (Jan 2 2009) uses total return: FDO 38.6, UST 32.0, HRB 25.8, AMGN 24.4, WMT 20.0, CELG 19.6, ROH 19.4. On price return (the ranking rule here), Celgene (no dividend) passes Wal-Mart (Yahoo +17.9%). UST's price return isn't separately sourced (total return 32.0%)."),
 2009: dict(status="verified", names=[("XL","XL Capital",None,3.95),("THC","Tenet Healthcare","THC",None),("AMD","Advanced Micro Devices","AMD",None),("F","Ford","F",None),("GNW","Genworth Financial","GNW",None)], sources=[SPDJI,"yahoo"], note="S&P DJI list has all of 2009's top 10. Yahoo agrees for THC, AMD, F and GNW."),
 2010: dict(status="verified", names=[("NFLX","Netflix","NFLX",None),("FFIV","F5 Networks","FFIV",None),("CMI","Cummins","CMI",None),("AIG","AIG","AIG",None),("ZION","Zions Bancorp","ZION",None)], sources=[IBT10,"yahoo"], note="AIG issued warrants to shareholders in Jan 2011; the Yahoo adjusted return used for 2011 may not count them."),
 2011: dict(status="probable", names=[("COG","Cabot Oil & Gas",None,1.0096),("EP","El Paso",None,0.9310),("ISRG","Intuitive Surgical","ISRG",None),("MA","MasterCard","MA",None),("BIIB","Biogen Idec","BIIB",None)], sources=[CNBC11,"yahoo","https://exa.ai/library/markets/stock/EP?date=2011-12-30&view=daily"], note="CNBC (Dec 28) listed COG, ISRG, MA and BIIB but left out El Paso because of the pending Kinder Morgan deal. El Paso was still a member at year-end, so it is included here: $13.76 to $26.57 (+93.1%, Exa daily closes)."),
 2012: dict(status="verified", names=[("PHM","PulteGroup","PHM",None),("S","Sprint Nextel",None,1.4231),("WHR","Whirlpool","WHR",None),("EXPE","Expedia","EXPE",None),("BAC","Bank of America","BAC",None)], sources=[FT12,"yahoo"], note="Source: The Fiscal Times, from Standard & Poor's data."),
 2013: dict(status="verified", names=[("NFLX","Netflix","NFLX",None),("MU","Micron","MU",None),("BBY","Best Buy","BBY",None),("DAL","Delta Air Lines","DAL",None),("ETFC","E*TRADE Financial",None,1.194)], sources=[FT13,SPDJI,"yahoo"], note=""),
 2014: dict(status="verified", names=[("LUV","Southwest Airlines","LUV",None),("EA","Electronic Arts",None,1.05),("EW","Edwards Lifesciences","EW",None),("AGN*","Allergan Inc. (old)",None,0.92),("AVGO","Avago Technologies","AVGO",None)], sources=[CNBC14,"yahoo"], note="The old Allergan (AGN) was acquired by Actavis on Mar 17 2015; see the 2015 outcome."),
 2015: dict(status="verified", names=[("NFLX","Netflix","NFLX",None),("AMZN","Amazon","AMZN",None),("ATVI","Activision Blizzard",None,0.941),("NVDA","NVIDIA","NVDA",None),("CVC","Cablevision",None,0.579)], sources=[FOOL15,"yahoo"], note="Cablevision's figure in the Motley Fool list may include dividends. Either way it ranks above #6 Hormel (Yahoo price +51.8%)."),
 2016: dict(status="verified", names=[("NVDA","NVIDIA","NVDA",None),("OKE","ONEOK","OKE",None),("FCX","Freeport-McMoRan","FCX",None),("NEM","Newmont","NEM",None),("AMAT","Applied Materials","AMAT",None)], sources=[FOOL16,"yahoo"], note="AMD rose more but was not an S&P 500 member at end-2016."),
 2017: dict(status="probable", names=[("NRG","NRG Energy","NRG",None),("ALGN","Align Technology","ALGN",None),("VRTX","Vertex Pharmaceuticals","VRTX",None),("WYNN","Wynn Resorts","WYNN",None),("BA","Boeing","BA",None)], sources=[FOOL17,"yahoo"], note="The Motley Fool (Dec 28) list includes First Solar, which was removed from the index in Mar 2017, so it is excluded here. Remaining order is from Yahoo year-end closes."),
 2018: dict(status="verified", names=[("AMD","Advanced Micro Devices","AMD",None),("ABMD","Abiomed",None,0.73),("FTNT","Fortinet","FTNT",None),("AAP","Advance Auto Parts","AAP",None),("TRIP","TripAdvisor","TRIP",None)], sources=[FOOL18,"yahoo"], note=""),
 2019: dict(status="verified", names=[("AMD","Advanced Micro Devices","AMD",None),("LRCX","Lam Research","LRCX",None),("KLAC","KLA","KLAC",None),("TGT","Target","TGT",None),("CMG","Chipotle","CMG",None)], sources=[MW19,"yahoo"], note=""),
 2020: dict(status="verified", names=[("TSLA","Tesla","TSLA",None),("ETSY","Etsy","ETSY",None),("NVDA","NVIDIA","NVDA",None),("PYPL","PayPal","PYPL",None),("LB","L Brands",None,1.055)], sources=[CNBC20,"yahoo"], note="L Brands became Bath & Body Works (BBWI) after spinning off Victoria's Secret in Aug 2021. Its 2021 return is BBWI's total return (Exa)."),
 2021: dict(status="verified", names=[("DVN","Devon Energy","DVN",None),("MRO","Marathon Oil",None,1.462),("MRNA","Moderna","MRNA",None),("FTNT","Fortinet","FTNT",None),("SBNY","Signature Bank",None,1.391)], sources=[CNBC21,"yahoo"], note="Signature Bank failed in Mar 2023, after its 2022 holding year."),
 2022: dict(status="verified", names=[("OXY","Occidental Petroleum","OXY",None),("HES","Hess",None,0.94),("MPC","Marathon Petroleum","MPC",None),("XOM","Exxon Mobil","XOM",None),("SLB","Schlumberger","SLB",None)], sources=[TL22,TM22,"yahoo"], note="Constellation Energy (+109%) is excluded because it was spun off in Feb 2022 and has no full-year return."),
 2023: dict(status="verified", names=[("NVDA","NVIDIA","NVDA",None),("META","Meta Platforms","META",None),("RCL","Royal Caribbean","RCL",None),("BLDR","Builders FirstSource","BLDR",None),("UBER","Uber","UBER",None)], sources=[MW23,"yahoo"], note=""),
 2024: dict(status="verified", names=[("PLTR","Palantir","PLTR",None),("VST","Vistra","VST",None),("NVDA","NVIDIA","NVDA",None),("UAL","United Airlines","UAL",None),("AXON","Axon Enterprise","AXON",None)], sources=[YC24,"yahoo"], note="GE Vernova (+150.6%) is excluded because it was spun off in Apr 2024."),
 2025: dict(status="verified", names=[("WDC","Western Digital","WDC",None),("MU","Micron","MU",None),("STX","Seagate","STX",None),("HOOD","Robinhood","HOOD",None),("WBD","Warner Bros. Discovery",None,28.82/10.57-1)], sources=[RW25,"yahoo","https://exa.ai/library/markets/stock/WBD?date=2025-12-31&view=daily"], note="SanDisk is excluded (no full year). Yahoo has no WBD history; its 2025 return is from the FactSet list (+173.2%), with the year-end close of $28.82 from Exa."),
}
DEAL = "Deal-based outcome: cash held at 0% after closing."
NEXT_HARD = {
 ("FDO",2009): (0.0874, exa("FDO",2009)),
 ("UST",2009): (69.50/69.38-1, "Altria closed its UST acquisition Jan 6 2009 at $69.50 cash (https://www.sec.gov/Archives/edgar/data/764180/000119312509001739/dex991.htm) vs $69.38 2008 close (Exa). "+DEAL),
 ("CELG",2009): (0.0072, exa("CELG",2009)),
 ("XL",2010): (0.2155, exa("XL",2010)),
 ("COG",2012): (0.3133, exa("COG",2012)),
 ("EP",2012): ((14.65+0.4187*35.33+0.640*3.86)/26.57-1, "El Paso acquired by Kinder Morgan May 25 2012 (https://www.sec.gov/Archives/edgar/data/1066107/000119312512246141/d357798dex991.htm). Mixed election: $14.65 cash + 0.4187 KMI + 0.640 warrants, valued at end-2012 KMI $35.33 and KMI.WS $3.86, vs 2011 close $26.57. Dividends (about 1pp) and proration ignored. "+DEAL),
 ("S",2013): (0.8959, exa("S",2013)+" (SoftBank deal Jul 2013; continuing Sprint shares)"),
 ("ETFC",2014): (0.2350, exa("ETFC",2014)),
 ("EA",2015): (0.4617, exa("EA",2015)),
 ("AGN*",2015): ((129.22+0.3683*312.50)/212.59-1, "Actavis acquired Allergan Mar 17 2015: $129.22 cash + 0.3683 Actavis (later Allergan plc) shares (https://www.sec.gov/Archives/edgar/data/1578845/000119312515096189/d894451d8k.htm). Valued at Allergan plc's 2015 close of $312.50 vs old Allergan's 2014 close of $212.59 (Exa). "+DEAL),
 ("ATVI",2016): (-0.0596, exa("ATVI",2016)),
 ("CVC",2016): (34.90/31.90-1, "Altice acquired Cablevision Jun 21 2016 at $34.90 cash (https://altice.net/documents-ir/altice-completes-acquisition-of-cablevision-systems-corporation) vs $31.90 2015 close (Exa). Two 2016 dividends ignored. "+DEAL),
 ("ABMD",2019): (-0.4752, exa("ABMD",2019)),
 ("LB",2021): (1.3622, exa("BBWI",2021)),
 ("MRO",2022): (0.6691, exa("MRO",2022)),
 ("SBNY",2022): (-0.6399, exa("SBNY",2022)),
 ("HES",2023): (0.0290, exa("HES",2023)),
 ("WBD",2026): (31.01666668/28.82-1, "Paramount Skydance acquired WBD Oct 6 2026 for $31.0167 cash per share (https://www.prnewswire.com/news-releases/paramount-completes-acquisition-of-warner-bros-discovery-creating-a-new-global-entertainment-leader-skydance-302899765.html) vs $28.82 2025 close (Exa). "+DEAL),
}

syms = sorted({v[2] for v in W.values() if v[2]} | {n[2] for t in TOP5.values() for n in t["names"] if n[2]} | {"^SP500TR"})
px = yf.download(syms, start="1995-12-01", end=datetime.date.today().isoformat(),  # completed sessions only
                 auto_adjust=False, progress=False)
close, adj = px["Close"], px["Adj Close"]
last_date = adj["^SP500TR"].dropna().index[-1]
LAST_YEAR = last_date.year

def ye(series, y):
    s = series[series.index <= f"{y}-12-31"].dropna()
    return s.iloc[-1] if len(s) else None

def ret(series, y):
    a, b = ye(series, y - 1), ye(series, y)
    return None if a is None or b is None else float(b / a - 1)

def tr(ticker, sym, y):
    """Total return of a winner in calendar year y -> (value, source, partial)."""
    if y > LAST_YEAR: return None, "", False
    if (ticker, y) in HARD_TR: v, s = HARD_TR[(ticker, y)]; return v, s, False
    if sym: return ret(adj[sym], y), "Yahoo Finance adj. close", y == LAST_YEAR
    return None, "", False

rows = []
for y in range(1996, LAST_YEAR + 1):
    t, name, sym, status, srcs, note = W[y]
    r = dict(year=y, ticker=t or "", company=name or "", status=status, note=note,
             sources=" | ".join(srcs))
    if t:
        if sym: r["return"] = ret(close[sym], y); r["return_src"] = "Yahoo Finance close (split-adj.)"
        else: r["return"] = HARD_PRICE[y]; r["return_src"] = HARD_PRICE_SRC.get(y, "S&P DJI via Visual Capitalist")
        n1, s1, p1 = tr(t, sym, y + 1)
        r["next_year"] = n1; r["next_year_partial"] = p1
        r["fwd_src"] = s1
    rows.append(r)
win = {r["year"]: r for r in rows}

# ---- Backtest (annual steps, total return). Starts Jan 1 1999 (kept from the original page; Strategy A alone could start 1998).
START = 1999
sp = {y: ret(adj["^SP500TR"], y) for y in range(START, LAST_YEAR + 1)}
def hold_ret(wy, y):
    w = win.get(wy)
    if not w or not w["ticker"]: return None
    return tr(w["ticker"], W[wy][2], y)[0]

A, S = [100.0], [100.0]
steps = []
for y in range(START, LAST_YEAR + 1):
    ra = hold_ret(y - 1, y)
    A.append(A[-1] * (1 + ra)); S.append(S[-1] * (1 + sp[y]))
    steps.append(dict(year=y, partial=(y == LAST_YEAR), sp500=sp[y], a_ticker=win[y - 1]["ticker"], a=ra))

# Strategy B: equal-weight last year's top 5, rebalanced each Jan 1.
top5_rows = []
for y in sorted(TOP5):
    t = TOP5[y]; names = []
    for tk, co, sym, hp in t["names"]:
        pr = hp if hp is not None else (ret(close[sym], y) if sym else None)
        if (tk, y + 1) in NEXT_HARD: nx, ns = NEXT_HARD[(tk, y + 1)]
        elif sym and y + 1 <= LAST_YEAR: nx, ns = ret(adj[sym], y + 1), "Yahoo Finance adj. close"
        else: nx, ns = None, ""
        names.append(dict(ticker=tk, company=co, ret=pr, next=nx, next_src=ns))
    complete = len(names) == 5 and all(n["next"] is not None for n in names) and t["status"] in ("verified", "probable")
    top5_rows.append(dict(year=y, status=t["status"], note=t["note"], sources=" | ".join(t["sources"]), names=names,
                          portfolio_next=(sum(n["next"] for n in names) / 5 if complete else None),
                          next_partial=(y + 1 == LAST_YEAR)))
T5 = {r["year"]: r for r in top5_rows}
# earliest start: first year Y such that every list from Y-1 .. LAST_YEAR-1 is complete
B_START = min(y for y in range(1997, LAST_YEAR + 1) if all(T5.get(k) and T5[k]["portfolio_next"] is not None for k in range(y - 1, LAST_YEAR)))
Bv = [100.0]; b_steps = []
for y in range(B_START, LAST_YEAR + 1):
    r = T5[y - 1]["portfolio_next"]; Bv.append(Bv[-1] * (1 + r))
    b_steps.append(dict(year=y, holdings=[n["ticker"] for n in T5[y - 1]["names"]], rets=[n["next"] for n in T5[y - 1]["names"]], b=r, partial=(y == LAST_YEAR)))

def stats(v, start=None):
    yrs = (last_date - datetime.datetime(start or START, 1, 1)).days / 365.25
    peak, mdd = v[0], 0
    for x in v: peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    return dict(ending=v[-1], cagr=(v[-1] / v[0]) ** (1 / yrs) - 1, max_drawdown=mdd)

out = dict(generated=datetime.date.today().isoformat(), last_close=last_date.date().isoformat(), start=START,
           winners=rows, steps=steps,
           series=dict(labels=[f"{START}-01-01"] + [f"{y}-12-31" for y in range(START, LAST_YEAR)] + [last_date.date().isoformat()],
                       sp500=S, a=A),
           stats=dict(sp500=stats(S), a=stats(A), b=stats(Bv, B_START)),
           b=dict(start=B_START, labels=[f"{B_START}-01-01"] + [f"{y}-12-31" for y in range(B_START, LAST_YEAR)] + [last_date.date().isoformat()],
                  values=Bv, steps=b_steps),
           top5=top5_rows)
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data/momentum.json").write_text(json.dumps(out, indent=1, default=float))
with open(ROOT / "data/momentum_winners.csv", "w", newline="") as f:
    cols = ["year", "ticker", "company", "return", "return_src", "next_year", "next_year_partial", "fwd_src", "status", "sources", "note"]
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
    for r in rows: w.writerow(r)
with open(ROOT / "data/momentum_top5.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["year", "rank", "ticker", "company", "return", "next_year_total_return", "next_year_source", "portfolio_next_year", "status", "sources", "note"])
    for r in top5_rows:
        for i, n in enumerate(r["names"], 1):
            w.writerow([r["year"], i, n["ticker"], n["company"], n["ret"], n["next"], n["next_src"], r["portfolio_next"], r["status"], r["sources"], r["note"]])
print("B start", B_START)
for k, v in out["stats"].items(): print(k, {a: round(b, 4) for a, b in v.items()})
