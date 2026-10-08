"""Keep financial references when deleting a driver, without credentials or GPS."""
import json
import time

SCHEMAS = (
    """CREATE TABLE IF NOT EXISTS closed_driver_finance(
        account_ref TEXT PRIMARY KEY, balance INTEGER NOT NULL,
        rides_json TEXT NOT NULL, review_status TEXT NOT NULL,
        closed_at BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS closed_driver_recharges(
        id TEXT PRIMARY KEY, account_ref TEXT NOT NULL,
        amount INTEGER NOT NULL, payment TEXT NOT NULL,
        status TEXT NOT NULL, created_at BIGINT NOT NULL, paid_at BIGINT
    )""",
)

def preserve(conn, driver):
    account_ref = driver['id']
    recharges = conn.execute('SELECT * FROM driver_recharges WHERE driver_id=%s', (account_ref,)).fetchall()
    rides = conn.execute('SELECT * FROM rides WHERE driver_id=%s', (account_ref,)).fetchall()
    balance = int(driver.get('balance') or 0)
    if not balance and not recharges and not rides:
        return False
    # An explicit allowlist prevents retaining names, phones, documents, tokens or GPS.
    fields = ('id','fare','fee','payment','payment_status','commission_charged',
              'deposit_amount','balance_due','deposit_paid_at','balance_paid_at','created_at','status')
    summaries = [{key: ride.get(key) for key in fields} for ride in rides]
    review = balance != 0 or any(row.get('status') != 'paid' for row in recharges)
    conn.execute("INSERT INTO closed_driver_finance(account_ref,balance,rides_json,review_status,closed_at) VALUES(%s,%s,%s,%s,%s)",
                 (account_ref,balance,json.dumps(summaries,separators=(',',':')),'needs_review' if review else 'archived',int(time.time())))
    for row in recharges:
        conn.execute("INSERT INTO closed_driver_recharges(id,account_ref,amount,payment,status,created_at,paid_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                     (row['id'],account_ref,int(row['amount']),row['payment'],row['status'],row['created_at'],row.get('paid_at')))
    return review
