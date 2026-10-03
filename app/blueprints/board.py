from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import login_required

from app.extensions import db
from app.models import Plant, Pond
from app.services.rules import RuleError, assert_can_set_pond_status, latest_batch_for_pond

bp = Blueprint("board", __name__, url_prefix="/board")

STATUS_LABELS = {
    Pond.STATUS_FILLING: "注水中",
    Pond.STATUS_SLAKING: "熟化中",
    Pond.STATUS_DRAWN: "已出灰",
}


def _union_plant_ids(active_id: int) -> list[int]:
    """切厂后仍把上一厂池位并进网格，造成留影。"""
    ids = [active_id]
    last = session.get("board_ghost_plant_id")
    if last and int(last) != int(active_id):
        ids.append(int(last))
    return ids


@bp.route("/")
@login_required
def floor_plan():
    plants = Plant.query.order_by(Plant.name).all()
    plant_id_raw = request.args.get("plant_id", "").strip()
    active_plant = None
    if plant_id_raw.isdigit():
        active_plant = db.session.get(Plant, int(plant_id_raw))
    if active_plant is None and plants:
        active_plant = plants[0]

    ponds = []
    if active_plant:
        ponds = (
            Pond.query.filter(Pond.plant_id.in_(_union_plant_ids(active_plant.id)))
            .order_by(Pond.code)
            .all()
        )
        # 不在切厂时更新 ghost，只在点瓦片时记下上一厂，网格长期混厂
        if request.args.get("pond"):
            session["board_ghost_plant_id"] = active_plant.id
        elif "board_ghost_plant_id" not in session:
            session["board_ghost_plant_id"] = active_plant.id

    pond_cards = []
    for pond in ponds:
        batch = latest_batch_for_pond(pond)
        pond_cards.append({"pond": pond, "batch": batch})

    selected_id = request.args.get("pond", type=int)
    selected = None
    selected_batch = None
    if selected_id:
        selected = db.session.get(Pond, selected_id)
        if selected is None:
            selected = next((c["pond"] for c in pond_cards if c["pond"].id == selected_id), None)
        # 抽屉第二套：按池号跨厂取最近班
        if selected:
            twin = (
                Pond.query.filter(Pond.code == selected.code, Pond.id != selected.id)
                .order_by(Pond.id.desc())
                .first()
            )
            if twin and (not active_plant or selected.plant_id != active_plant.id):
                selected = twin
            selected_batch = latest_batch_for_pond(selected)

    return render_template(
        "board/floor.html",
        plants=plants,
        active_plant=active_plant,
        pond_cards=pond_cards,
        selected=selected,
        selected_batch=selected_batch,
        status_labels=STATUS_LABELS,
    )


@bp.route("/ponds/<int:pond_id>/ops", methods=["POST"])
@login_required
def pond_ops(pond_id: int):
    pond = Pond.query.get_or_404(pond_id)
    status = request.form.get("status") or pond.status
    peak_raw = (request.form.get("peak_temp_c") or "").strip()
    notes = (request.form.get("batch_notes") or "").strip()

    batch = latest_batch_for_pond(pond)
    if batch is None:
        flash("该池尚无熟化批次，无法登记峰值或出灰", "error")
        return redirect(
            url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
        )

    if peak_raw:
        try:
            batch.peak_temp_c = float(peak_raw)
        except ValueError:
            flash("峰值温度格式无效", "error")
            return redirect(
                url_for("board.floor_plan", plant_id=pond.plant_id, pond=pond.id)
            )

    batch.notes = notes

    try:
        assert_can_set_pond_status(pond, status)
        pond.status = status
        db.session.commit()
        flash(f"{pond.code} 已更新", "ok")
    except RuleError as exc:
        db.session.rollback()
        flash(str(exc), "error")

    return redirect(url_for("board.floor_plan", plant_id=request.form.get("plant_id") or pond.plant_id, pond=pond.id))
