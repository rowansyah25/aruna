-- =====================================================================
-- ARUNA AI - FUTURES: a reference price on every verdict
--
-- 0015 made a half-plan unrepresentable: a non-PLAN verdict carries no
-- entry, no stop, no leverage. That rule was right and it went one column
-- too far.
--
-- A WAIT with no price attached cannot be scored. "ARUNA stood aside" is
-- not a judgeable claim; "ARUNA stood aside at 63,000" is, because only the
-- second says what the market afterwards did relative to anything. Twenty-
-- four WAITs were stored before this column existed and not one of them can
-- ever be assessed.
--
-- The distinction the CHECK in 0015 was really reaching for is not
-- "numbers versus no numbers" - it is INSTRUCTIONS versus OBSERVATIONS.
-- Entry, stop, target and leverage tell a reader what to do, and a refusal
-- must not carry them. The mark price at the moment of the verdict tells a
-- reader what was true, and a refusal is worthless without it.
-- =====================================================================

ALTER TABLE futures_plans
    ADD COLUMN reference_price DECIMAL(30,12) NULL AFTER horizon_hours;

-- Backfill what can be recovered: a stored PLAN was formed at its own entry
-- price. The WAITs and refusals already written cannot be recovered - the
-- observation was never taken - and they stay NULL rather than receiving an
-- invented one. A guessed reference would make those rows look scoreable
-- while scoring them against a number nobody observed.
UPDATE futures_plans
    SET reference_price = entry
    WHERE verdict = 'PLAN' AND entry IS NOT NULL;
