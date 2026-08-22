-- =====================================================================
-- ARUNA AI - PHASE 7 corrections
--
-- One rename. `r_multiple` claimed to be an R-multiple, which in trading
-- means net PnL divided by the risk taken - the distance to a stop loss.
-- ARUNA has no stop loss, and the figure was actually computed against the
-- distance to the modelled target. Two different numbers with two different
-- meanings, and the wrong name flatters every trade that ran without a stop.
--
-- Renamed rather than dropped: the column holds real recorded values, and
-- what they measure has not changed - only what they are called.
-- =====================================================================

ALTER TABLE paper_trades
    CHANGE COLUMN r_multiple target_multiple DECIMAL(14,4) NULL;
