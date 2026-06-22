import truststore
truststore.inject_into_ssl()

import os
import logging
from dataclasses import dataclass
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "축의리스트")
NAME_COLUMN = os.environ.get("NAME_COLUMN", "이름")
AMOUNT_COLUMN = os.environ.get("AMOUNT_COLUMN", "액")

SHEET_LABELS: dict[str, str] = {
    "카톡": "💬 카톡",
    "봉투": "✉️ 봉투",
    "기업": "🏢 기업",
}
TARGET_SHEETS: set[str] = set(SHEET_LABELS.keys())


@dataclass(frozen=True)
class SearchResult:
    sheet_name: str
    record: dict


def _prepare_auth_files() -> None:
    if not os.path.exists("client_secret.json"):
        secret = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not secret:
            raise ValueError("client_secret.json 또는 GOOGLE_CLIENT_SECRET_JSON 환경변수가 필요합니다.")
        with open("client_secret.json", "w", encoding="utf-8") as f:
            f.write(secret)

    if not os.path.exists("token.json"):
        token = os.environ.get("GOOGLE_TOKEN_JSON")
        if not token:
            raise ValueError("token.json 또는 GOOGLE_TOKEN_JSON 환경변수가 필요합니다.")
        with open("token.json", "w", encoding="utf-8") as f:
            f.write(token)


def _open_spreadsheet() -> gspread.Spreadsheet:
    _prepare_auth_files()
    client = gspread.oauth(
        credentials_filename="client_secret.json",
        authorized_user_filename="token.json",
    )
    return client.open(SPREADSHEET_NAME)


def _sheet_to_records(sheet: gspread.Worksheet) -> list[dict]:
    rows = sheet.get_all_values()
    if not rows:
        return []
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:] if any(row)]


def search_all_sheets(query: str) -> list[SearchResult]:
    spreadsheet = _open_spreadsheet()
    results: list[SearchResult] = []
    for sheet in spreadsheet.worksheets():
        if sheet.title not in TARGET_SHEETS:
            continue
        for record in _sheet_to_records(sheet):
            if query in str(record.get(NAME_COLUMN, "")):
                results.append(SearchResult(sheet_name=sheet.title, record=record))
    return results


def get_stats_per_sheet() -> list[dict]:
    spreadsheet = _open_spreadsheet()
    stats: list[dict] = []
    for sheet in spreadsheet.worksheets():
        if sheet.title not in TARGET_SHEETS:
            continue
        records = _sheet_to_records(sheet)
        total_amount = 0
        for r in records:
            try:
                val = str(r.get(AMOUNT_COLUMN, "")).replace(",", "").replace("원", "").strip()
                if val:
                    total_amount += int(val)
            except (ValueError, TypeError):
                pass
        stats.append({
            "sheet": sheet.title,
            "count": len(records),
            "total": total_amount,
        })
    return stats


def _sheet_label(sheet_name: str) -> str:
    return SHEET_LABELS.get(sheet_name, f"📄 {sheet_name}")


def _format_record(record: dict) -> str:
    name = record.get(NAME_COLUMN, "")
    amount = record.get(AMOUNT_COLUMN, "")
    return f"{name} / {amount}"


def _format_search_results(results: list[SearchResult], query: str) -> str:
    if not results:
        return f"❌ '{query}'에 해당하는 내역이 없습니다."

    header = f"🔍 '{query}' 검색 결과 ({len(results)}건)\n{'─' * 30}"
    lines = [header]
    for r in results:
        label = _sheet_label(r.sheet_name)
        lines.append(f"[{label}] {_format_record(r.record)}")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *축의리스트 검색 봇*\n\n"
        "이름(또는 일부)을 입력하면 카톡·봉투·기업 시트 전체에서 검색합니다.\n\n"
        "명령어:\n"
        "/help — 사용법\n"
        "/stats — 시트별 통계",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *사용 방법*\n\n"
        "이름 또는 이름 일부를 입력하세요.\n"
        "카톡·봉투·기업 시트를 모두 검색하고\n"
        "결과마다 어느 시트인지 함께 표시합니다.\n\n"
        "예시:\n"
        "• `홍길동` → 정확한 이름 검색\n"
        "• `홍` → '홍'이 포함된 모든 이름 검색",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        stats = get_stats_per_sheet()
        lines = [f"📊 *시트별 통계*\n{'─' * 30}"]
        grand_count = 0
        grand_total = 0
        for s in stats:
            label = _sheet_label(s["sheet"])
            line = f"{label}: {s['count']}명"
            if s["total"] > 0:
                line += f" / {s['total']:,}원"
            lines.append(line)
            grand_count += s["count"]
            grand_total += s["total"]

        lines.append(f"{'─' * 30}")
        summary = f"합계: {grand_count}명"
        if grand_total > 0:
            summary += f" / {grand_total:,}원"
        lines.append(summary)

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Stats error: {e}", exc_info=True)
        await update.message.reply_text("⚠️ 통계 조회 중 오류가 발생했습니다.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.message.text.strip()
    if not query:
        await update.message.reply_text("검색할 이름을 입력해주세요.")
        return

    try:
        results = search_all_sheets(query)
        response = _format_search_results(results, query)
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        response = "⚠️ 검색 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

    await update.message.reply_text(response)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN 환경 변수가 설정되지 않았습니다.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("봇 시작됨...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
