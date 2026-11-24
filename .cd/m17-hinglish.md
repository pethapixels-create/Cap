Title: Payments Blocker and Load Test
## Updates by person
- **Sneha Iyer:** Reported 14 double debits among 1,750 staging UPI test transactions (about 0.8%) when Dhan Path callbacks exceed the 10-second timeout. The retry path lacks an idempotency key, and reconciliation matches by order ID rather than gateway transaction ID. She will raise a support ticket about callback delays and share the response; she will also send Ananya the list of nine production customers who were manually refunded.
- **Rohit Mehta:** Identified the missing idempotency key in the retry path. He will fix retries and change reconciliation to match by gateway transaction ID, then hand both changes to QA together by Wednesday, November 25.
- **Vikram Rao:** Reported that staging reached 3,400 OPM in a previous run before p95 latency exceeded two seconds; the order-service database was the bottleneck. He will investigate whether staging can reach the agreed test target without infrastructure changes and run the load test.
- **Ananya Kulkarni:** Will check with the finance head about the budget for two order-service database read replicas, estimated at about ₹1.8 lakh per month.

## Blockers
- **v4.12 release:** Blocked until the double-debit fix passes QA. Combo Thali and the payments refactor will ship together; there will be no separate feature-flagged Combo Thali release.
- **Callback delays:** The cause of Dhan Path callbacks exceeding 10 seconds is unknown; it could be on the gateway side or in the staging network. Sneha will raise a support ticket today and share the response when it arrives.
- **Read replicas:** Approval is pending finance review. The proposed two replicas would cost about ₹1.8 lakh per month.
- **Release date:** No date is committed. November 26 was considered too tight; December 1 was suggested but not confirmed. The team will decide at Monday’s stand-up based on fix progress, and Ananya will tell Product a date will follow Monday.
- **Unidentified production double debits:** Nine customers were manually refunded, but it is unknown whether other customers were affected. Whether to backfill or otherwise check older data remains open.
- **Backup gateway:** Switching UPI to the backup gateway remains undecided. It would change settlement from T+1 to T+2 and may draw objections from Finance.

## Follow-ups
- **Rohit Mehta:** Hand both fixes to QA together by Wednesday, November 25: add an idempotency key to payment retries and change reconciliation matching from order ID to gateway transaction ID.
- **Sneha Iyer:** Raise a Dhan Path support ticket about callbacks taking more than 10 seconds today, and share the response with the team when received.
- **Sneha Iyer:** Send Ananya the list of the nine production customers who were manually refunded.
- **Vikram Rao:** Run an 8,000 OPM staging baseline load test on the current setup and share p95 latency and database metrics by Tuesday, November 24. Include the payment step using only the Dhan Path sandbox, not the production gateway.
- **Vikram Rao:** Check whether staging can realistically reach 8,000 OPM without infrastructure changes, and check the sandbox rate limit.
- **Rohit Mehta:** Share the Dhan Path sandbox rate-limit information with Vikram.
- **Ananya Kulkarni:** Check with the finance head about the budget for two read replicas.