-- =====================================================================
-- ARUNA AI - correction: append-only triggers that lost their message
--
-- MySQL caps SIGNAL ... SET MESSAGE_TEXT at 128 characters. Two of the
-- append-only triggers were written well past that, so instead of the
-- message MySQL raised:
--
--     1648  Data too long for condition item 'MESSAGE_TEXT'
--
-- The write was still refused, which is the important half. But the entire
-- reason for a custom message - telling whoever hit it *why* the table
-- refuses updates, and what to do instead - was destroyed, and replaced
-- with an error that reads like a bug in ARUNA rather than a deliberate
-- guarantee.
--
-- Rewritten under the limit. The reasoning that no longer fits lives in
-- the migration that created each table (0010, 0012) and in the module
-- docstrings; a trigger message has room for the rule, not the argument.
-- =====================================================================

DROP TRIGGER calibration_snapshots_no_update;

CREATE TRIGGER calibration_snapshots_no_update
    BEFORE UPDATE ON calibration_snapshots
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'calibration_snapshots is append-only: a measurement records what was true at one moment (SPEC 29)';

DROP TRIGGER backtest_runs_no_update;

CREATE TRIGGER backtest_runs_no_update
    BEFORE UPDATE ON backtest_runs
    FOR EACH ROW
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'backtest_runs is append-only: PHASE 10 audits variant choices against these rows (SPEC 35, 38)';
