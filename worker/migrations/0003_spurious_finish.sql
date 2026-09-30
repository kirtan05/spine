-- A finish that reverses within minutes is undone (see recordBookStatus in
-- src/routes/syncs.ts). Undoing the most recent crossing must restore the one
-- before it, which is not recoverable from the other columns.
ALTER TABLE book_status ADD COLUMN prior_finished_at INTEGER;
