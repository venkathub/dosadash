"""Only predictions recorded before a target date can be monitoring evidence."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from dosadash_api.db.models import ForecastHealth, ForecastObservation, MenuItem
from dosadash_api.worker.tasks import _monitor_demand, _upsert_forecasts
from dosadash_ml.forecasting.predict import ForecastRow


async def test_monitor_excludes_backfilled_predictions_and_keeps_immutable_snapshot(db_session):
    today = date(2026, 10, 18)
    item_ids = list((await db_session.scalars(select(MenuItem.id))).all())
    for offset in range(28):
        day = today - timedelta(days=offset + 1)
        for item_id in item_ids:
            db_session.add(
                ForecastObservation(
                    item_id=item_id,
                    date=day,
                    issued_day=day,
                    published_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
                    + timedelta(hours=3),
                    predicted_qty=10,
                    model_version="demo/v1",
                )
            )
    await db_session.commit()
    report = await _monitor_demand(db_session.bind, today)
    assert report["complete_days"] == 0 and report["status"] == "insufficient_data"
    assert await db_session.get(ForecastHealth, today)

    future = today + timedelta(days=900)
    rows = [ForecastRow(item_id=item_ids[0], date=future, predicted_qty=10)]
    await _upsert_forecasts(db_session.bind, rows, "first")
    await _upsert_forecasts(
        db_session.bind,
        [ForecastRow(item_id=item_ids[0], date=future, predicted_qty=999)],
        "second",
    )
    observation = await db_session.scalar(
        select(ForecastObservation).where(ForecastObservation.date == future)
    )
    assert observation.predicted_qty == 10 and observation.model_version == "first"
