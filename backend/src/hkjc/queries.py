"""
HKJC SQL

The seven queries from "5 Data Extraction - Real Time.ipynb", kept as close
to the originals as possible so they can be diffed against the notebook.
The only changes are that the match-id list is injected through a helper
instead of an inline f-string, and the odds/market queries now also select
the line and combination ids the notebook already relied on downstream.

Three connections are involved, exactly as in the notebook:

    conn_qfm   qfm_outbound_db   pools, odds, incidents, match profile
    conn_bis   bis1_fbuser_db    algo_param (trading parameters)
    conn_bis2  QFM               ticket_bets_dtl_agg_mem (investments)

Change Log:
-----------
2026-08-30      Initialize (port of notebook cells 2 and 4-9)
"""


def id_list(match_ids) -> str:
    """Render a match-id list for an IN clause the way the notebook does."""
    return "','".join(str(int(i)) for i in match_ids)


# Bet-type enum shared by the odds and market queries.
BET_TYPE_CTE = """
WITH EnumDataCTE (EnumValue, EnumString) AS (
SELECT * FROM (VALUES
    (0,'None'),(1,'HAD'),(2,'FHAD'),(3,'HILO'),(4,'FHLO'),(5,'CHLO'),
    (6,'HDC'),(7,'HHAD'),(8,'HFT'),(9,'CRS'),(10,'FCRS'),(11,'OOE'),
    (12,'TTG'),(13,'FTS'),(14,'TQL'),(15,'NTS'),(16,'TPS'),(17,'FGS'),
    (18,'CHP'),(19,'GPF'),(20,'GPW'),(31,'DHCP'),(32,'JKC'),(33,'TNC'),
    (34,'TSPC'),(35,'MSPC'),(36,'ETNTS'),(37,'ETHAD'),(38,'ETHILO'),
    (39,'ETCHLO'),(40,'ETHDC'),(41,'ETCRS'),(42,'ETTTG'),(43,'TESTBTG'),
    (44,'SGA'),(45,'FCHLO'),(46,'FHDC'),(47,'FCHDC'),(48,'CHDC'),
    (49,'ETCHDC'),(50,'ETHHAD'),(51,'NGS'),(52,'LGS'),(53,'AGS'),
    (54,'ETNGS'),(55,'ETLGS'),(56,'ETAGS')
) AS v(EnumValue, EnumString)
)
"""

BOOKMAKER_CTE = """
, BookmakerNameCTE (EnumValue, EnumString) AS (
SELECT * FROM (VALUES
    (0,'None'),(1,'BM10BET'),(2,'BetISN'),(3,'Bwin'),(4,'Centrebet'),
    (5,'Coral'),(6,'IBCBET'),(7,'Ladbrokes'),(8,'PaddyPower'),
    (9,'Sbobet_com'),(10,'SNAI'),(11,'WilliamHill'),(12,'Bet365'),
    (13,'PinnacleSports'),(14,'HKJC'),(15,'BM12Bet'),(16,'BM188Bet'),
    (17,'BM1XBet'),(18,'BM855Crown'),(19,'Bet855'),(20,'BetCityRU'),
    (21,'BETDAQ'),(22,'Betway'),(23,'BoyleSports'),(24,'DafaBet'),
    (25,'Easybets'),(26,'Fonbet'),(27,'MacauSlot'),(28,'Mansion88'),
    (29,'Marathonbet'),(30,'Matchbook'),(31,'Nextbet'),(32,'Olimpbet'),
    (33,'PokerStars'),(34,'Singbet'),(35,'Skybet'),(36,'Sportingbet'),
    (37,'Sporttery'),(38,'TheGreek'),(39,'TonyBet'),(40,'TouTou'),
    (41,'Unibet'),(42,'V9BET'),(43,'Victor')
) AS v(EnumValue, EnumString)
)
"""


# --- 1. Active pools (notebook cell 2) -------------------------------------
ACTIVE_POOLS = """
SELECT e.event_id [match_id], p.pool_id
  FROM [qfm_outbound_db].[dbo].[pool_h] p
  inner join [dbo].[pool_event_leg_h] l on p.pool_id = l.pool_id
  inner join [dbo].[event_level2_profile_h] e on e.event_id = l.event_id
  where start_sell_datetime <= GETDATE()
  and l.is_deleted = 0 and p.is_deleted = 0 and l.event_type = 2
  and e.is_deleted = 0
"""


# --- 2. Match information (notebook cell 4) --------------------------------
def match_info(match_ids) -> str:
    return """
select
     m.event_id [MatchId]
    ,m.frontend_id [FrontendID]
    ,m.parent_event_id [LeagueId]
    ,CASE WHEN h.event_id IS NOT NULL THEN h.full_name_en ELSE c.full_name_en END [LeagueName]
    ,CASE WHEN h.event_id IS NOT NULL THEN h.code ELSE c.code END [LeagueCode]
    ,m.venue_id [VenueId]
    ,venue.full_name_en [VenueName]
    ,venue.code3 [Country]
    ,CASE WHEN h.event_id IS NOT NULL THEN h.season ELSE c.season END [Season]
    ,home.full_name_en [HomeName]
    ,home.name_profile_id [HomeId]
    ,away.full_name_en [AwayName]
    ,away.name_profile_id [AwayId]
    ,m.start_datetime [KOTime]
    ,ISNULL(scoreft.home_result_score,-1) [HomeScoreFT]
    ,ISNULL(scoreft.away_result_score,-1) [AwayScoreFT]
    ,ISNULL(scoreht.home_result_score,-1) [HomeScoreHT]
    ,ISNULL(scoreht.away_result_score,-1) [AwayScoreHT]
    ,ISNULL(cornerft.home_result_score,-1) [HomeCornerFT]
    ,ISNULL(cornerft.away_result_score,-1) [AwayCornerFT]
    ,ISNULL(cornerht.home_result_score,-1) [HomeCornerHT]
    ,ISNULL(cornerht.away_result_score,-1) [AwayCornerHT]
    ,ISNULL(ycft.home_result_score,-1) [HomeYellowFT]
    ,ISNULL(ycft.away_result_score,-1) [AwayYellowFT]
    ,ISNULL(ycht.home_result_score,-1) [HomeYellowHT]
    ,ISNULL(ycht.away_result_score,-1) [AwayYellowHT]
    ,CASE WHEN yrcft.home_result_score IS NULL AND rcft.home_result_score IS NULL THEN -1
          ELSE ISNULL(yrcft.home_result_score,0) + ISNULL(rcft.home_result_score,0) END [HomeRedFT]
    ,CASE WHEN yrcft.away_result_score IS NULL AND rcft.away_result_score IS NULL THEN -1
          ELSE ISNULL(yrcft.away_result_score,0) + ISNULL(rcft.away_result_score,0) END [AwayRedFT]
    ,CASE WHEN yrcht.home_result_score IS NULL AND rcht.home_result_score IS NULL THEN -1
          ELSE ISNULL(yrcht.home_result_score,0) + ISNULL(rcht.home_result_score,0) END [HomeRedHT]
    ,CASE WHEN yrcht.away_result_score IS NULL AND rcht.away_result_score IS NULL THEN -1
          ELSE ISNULL(yrcht.away_result_score,0) + ISNULL(rcht.away_result_score,0) END [AwayRedHT]
    ,CASE WHEN EXISTS (
        SELECT 1 FROM [dbo].[event_op_status_change_h] b with(nolock)
        WHERE b.event_id = m.event_id AND b.event_operation_status in (38,39,40,41,42,43)
     ) THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS IsVoid
from [dbo].[event_level2_profile_h] m WITH (NOLOCK)
LEFT JOIN [dbo].[event_level1_profile_c] c WITH (NOLOCK) on m.parent_event_id = c.event_id
LEFT JOIN [dbo].[event_level1_profile_h] h WITH (NOLOCK) on m.parent_event_id = h.event_id
OUTER APPLY (select top 1 full_name_en,name_profile_id from [dbo].[event_participant] p WITH (NOLOCK)
             where p.name_profile_id = m.home_participant_id order by p.name_profile_version desc) AS home
OUTER APPLY (select top 1 full_name_en,name_profile_id from [dbo].[event_participant] p WITH (NOLOCK)
             where p.name_profile_id = m.away_participant_id order by p.name_profile_version desc) AS away
OUTER APPLY (select top 1 full_name_en,name_profile_id, code3 from [dbo].[event_location] l WITH (NOLOCK)
             where l.name_profile_id = m.venue_id order by l.name_profile_version desc) AS venue
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 1 and r.stage_id = 5 order by r.last_modified_datetime desc) AS scoreft
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 1 and r.stage_id = 3 order by r.last_modified_datetime desc) AS scoreht
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 2 and r.stage_id = 5 order by r.last_modified_datetime desc) AS cornerft
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 2 and r.stage_id = 3 order by r.last_modified_datetime desc) AS cornerht
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 3 and r.stage_id = 5 order by r.last_modified_datetime desc) AS ycft
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 3 and r.stage_id = 3 order by r.last_modified_datetime desc) AS ycht
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 4 and r.stage_id = 5 order by r.last_modified_datetime desc) AS yrcft
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 4 and r.stage_id = 3 order by r.last_modified_datetime desc) AS yrcht
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 5 and r.stage_id = 5 order by r.last_modified_datetime desc) AS rcft
OUTER APPLY (select top 1 event_id,home_result_score, away_result_score from [dbo].[event_level2_result_h] r WITH (NOLOCK)
             where r.event_id = m.event_id and r.result_type = 5 and r.stage_id = 3 order by r.last_modified_datetime desc) AS rcht
where m.event_id IN ('{ids}')
and (m.frontend_id is not null and m.frontend_id != '')
""".format(ids=id_list(match_ids))


# --- 3. Trading parameters (notebook cell 5) -------------------------------
def algo_params(match_ids) -> str:
    return """
select * from [QFM].[dbo].[algo_param]
where EventLevel2ID IN ('{ids}')
order by EventLevel2ID, EventTime
""".format(ids=id_list(match_ids))


# --- 4. Incidents (notebook cell 6) ----------------------------------------
def incidents(match_ids) -> str:
    return """
SELECT * FROM [dbo].[event_level2_incident_h]
WHERE [event_id] IN ('{ids}')
""".format(ids=id_list(match_ids))


# --- 5. HKJC odds and true odds (notebook cell 7) --------------------------
def hkjc_odds(match_ids) -> str:
    return BET_TYPE_CTE + """
select
    e.event_id [match_id],
    p.bet_type_code,
    bt.EnumString,
    e.pool_id,
    l.line_id,
    l.line_label,
    c.combination_id,
    c.combination_string,
    o.odds,
    o.effective_datetime,
    tt.odds [true_odds]
from [dbo].[pool_event_leg_h] e with(nolock)
    inner join [dbo].[pool_h] p with(nolock) on e.pool_id = p.pool_id
    left join [dbo].[pool_line_h] l with(nolock) on l.pool_id = e.pool_id and l.is_deleted = 0
    left join [dbo].[pool_combination_h] c with(nolock) on c.pool_id = e.pool_id and c.line_id = l.line_id and c.is_deleted = 0
    left join [dbo].[pool_odds_change_h] o with(nolock) on o.pool_id = c.pool_id and o.line_id = c.line_id and o.combination_id = c.combination_id and o.is_deleted = 0
    left join EnumDataCTE bt on bt.EnumValue = p.bet_type_code
    outer apply (
        select top 1 *
        from [dbo].[pool_odds_true_change_h] t with(nolock)
        where t.pool_id = o.pool_id and t.line_id = o.line_id and t.combination_id = o.combination_id and t.odds > 0
        and t.effective_datetime <= o.effective_datetime
        order by t.effective_datetime desc
    ) tt
where e.event_id IN ('{ids}') and e.is_deleted = 0
""".format(ids=id_list(match_ids))


# --- 6. External bookmaker odds (notebook cell 8) --------------------------
def market_odds(match_ids) -> str:
    return BET_TYPE_CTE + BOOKMAKER_CTE + """
select
    e.event_id [match_id],
    p.bet_type_code,
    e.pool_id,
    l.line_id,
    l.line_label,
    c.combination_id,
    c.combination_string,
    o.bookmaker,
    o.odds,
    o.effective_datetime,
    bn.EnumString [bookmaker_str],
    bt.EnumString [bet_type_code_str]
from [dbo].[pool_event_leg_h] e with(nolock)
    inner join [dbo].[pool_h] p with(nolock) on e.pool_id = p.pool_id
    left join [dbo].[pool_line_h] l with(nolock) on l.pool_id = e.pool_id
    left join [dbo].[pool_combination_h] c with(nolock) on c.pool_id = e.pool_id and c.line_id = l.line_id
    left join [dbo].[pool_odds_external_change_h] o with(nolock) on o.pool_id = c.pool_id and o.line_id = c.line_id and o.combination_id = c.combination_id
    left join EnumDataCTE bt on bt.EnumValue = p.bet_type_code
    left join BookmakerNameCTE bn on bn.EnumValue = o.bookmaker
where e.event_id IN ('{ids}')
and e.is_deleted = 0 and p.is_deleted = 0
and l.is_deleted = 0 and c.is_deleted = 0 and o.is_deleted = 0
""".format(ids=id_list(match_ids))


# --- 7. Investments (notebook cell 9) --------------------------------------
def investments(match_ids) -> str:
    return """
select p.PoolID [pool_id], p.PoolName [pool_name], e.EventID [match_id], e.FrontendID [frontend_id]
  , t.EngShortName [tournament], h.FullNameEn [h_team], a.FullNameEn [a_team]
  , ht.HomeResultScore [ht_home_score], ht.AwayResultScore [ht_away_score]
  , ft.HomeResultScore [ft_home_score], ft.AwayResultScore [ft_away_score]
  , e.PlayDateTime [kickoff_date]
  , i.IntervalTimeStart [start_sell_time]
  , i.CombinationID [comb_id]
  , i.LineID [line_no]
  , null [odds_id]
  , i.TotalInvestment_PE + i.TotalInvestment_FH + i.TotalInvestment_SH [turnover]
  , null [dividend]
  , null [es_dividend]
  , l.CombStr [odds_combination]
  , null [odds]
  , i.TicketCount_PE + i.TicketCount_FH + i.TicketCount_SH [ticket_count]
  from [dbo].[event_level2_profile] e
  inner join [dbo].[event_level1_profile] t on e.EventLevel1ID = t.EventID
  inner join [dbo].[pool_info] p on e.EventID = p.EventID
  inner join [dbo].[pool_line_comb] l on p.PoolID = l.PoolID
  inner join [dbo].[ticket_bets_dtl_agg_mem] i WITH (SNAPSHOT)
        on l.PoolID = i.PoolID and i.LineID = l.LineID and i.CombinationID = l.CombID
  cross apply (select top 1 * FROM [dbo].[participant_profile] pp
               WHERE e.HomeParticipantID = pp.NameProfileID) h
  cross apply (select top 1 * FROM [dbo].[participant_profile] pp
               WHERE e.AwayParticipantID = pp.NameProfileID order by pp.ModifyTime desc) a
  Outer apply (select top 1 * FROM [dbo].[event_level2_result] r
               WHERE e.EventID = r.EventID and r.ResultType = 1 and StageID <= 3
               order by r.ResultInputTime desc) ht
  Outer apply (select top 1 * FROM [dbo].[event_level2_result] r
               WHERE e.EventID = r.EventID and r.ResultType = 1
               order by r.ResultInputTime desc) ft
  where e.[IsDeleted] = 0 and e.FrontendID != '' and e.FrontendID is not null
  and (p.IsDeleted = 0 or p.IsDeleted is null)
  and e.EventID IN ('{ids}')
""".format(ids=id_list(match_ids))
