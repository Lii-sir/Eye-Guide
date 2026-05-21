"""图片搜索与下载工具模块。

从 Openverse 和 Wikimedia Commons 搜索并下载开源许可的障碍物图片，
按分类构建视障出行辅助数据集。

数据流程：
  分类查询配置 → 多源搜索(Openverse/Wikimedia) → 候选过滤(尺寸/格式/去重) → 按分类保存 → 元数据输出

输出结构：
  output_dir/
    images/{category}/...   按分类存放图片
    metadata.jsonl          逐行 JSON 元数据
    metadata.csv            CSV 格式元数据
    summary.json            汇总统计
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import mimetypes
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError


LOGGER = logging.getLogger("eyeguide.image_search")

# HTTP 请求 User-Agent 标识
USER_AGENT = "EyeGuideDatasetBuilder/0.2 (+https://github.com/openai)"
# Openverse 图片搜索 API 地址
OPENVERSE_URL = "https://api.openverse.org/v1/images/"
# Wikimedia Commons API 地址
WIKIMEDIA_URL = "https://commons.wikimedia.org/w/api.php"

# 支持的光栅图片扩展名集合
RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# Content-Type 到文件扩展名的映射
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}

# 默认分类查询配置：键为障碍物类别，值为该类别下的搜索关键词列表
DEFAULT_CATEGORY_QUERIES: dict[str, list[str]] = {
    "tactile_paving_blocked": [           # 盲道被遮挡
        "tactile paving blocked sidewalk",
        "trash bin blocking tactile paving",
        "bike parked on tactile paving",
        "obstacle on tactile paving",
    ],
    "parked_bicycle": [                   # 自行车占道
        "parked bicycle blocking sidewalk",
        "bicycle blocking pedestrian path",
        "bike obstructing walkway",
    ],
    "parked_scooter": [                   # 滑板车占道
        "parked scooter blocking sidewalk",
        "electric scooter obstructing walkway",
        "scooter blocking pedestrian path",
    ],
    "construction_barrier": [             # 施工围挡
        "construction barrier on sidewalk",
        "construction fence blocking sidewalk",
        "roadwork barrier pedestrian walkway",
    ],
    "pole_or_bollard": [                  # 路柱/标杆障碍
        "bollard obstacle on sidewalk",
        "street sign pole obstacle on sidewalk",
        "pole blocking pedestrian walkway",
    ],
    "pothole_or_manhole": [              # 坑洞/井盖
        "pothole sidewalk hazard",
        "open manhole sidewalk hazard",
        "damaged sidewalk hole pedestrian",
    ],
    "stairs_or_dropoff": [               # 台阶/落差
        "stairs sidewalk obstacle",
        "sudden step pedestrian hazard",
        "drop off sidewalk hazard",
    ],
    "uneven_surface": [                   # 路面不平
        "uneven pavement sidewalk hazard",
        "broken sidewalk pedestrian hazard",
        "cracked pavement walkway obstacle",
    ],
    "overhead_branch": [                  # 头部高度树枝
        "tree branch head height sidewalk obstacle",
        "low hanging branch sidewalk hazard",
        "branch blocking pedestrian path",
    ],
    "curb_without_ramp": [               # 缺少坡道的路缘
        "curb without ramp sidewalk hazard",
        "missing curb ramp pedestrian access",
        "high curb pedestrian barrier",
    ],
    "crosswalk_obstacle": [              # 人行横道障碍
        "crosswalk obstacle for pedestrians",
        "obstacle at crosswalk",
        "blocked pedestrian crossing",
    ],
}


@dataclass(slots=True)
class ImageCandidate:
    """图片候选项数据类，保存从搜索 API 获取的单张图片信息。"""

    provider: str               # 数据源标识: "openverse" 或 "wikimedia"
    query: str                  # 对应的搜索关键词
    title: str                  # 图片标题
    image_url: str              # 图片下载地址
    source_page: str | None     # 图片来源页面 URL
    license: str | None         # 许可证名称 (如 "CC BY 2.0")
    license_url: str | None     # 许可证详情 URL
    creator: str | None         # 创作者名称
    width: int | None           # 图片宽度(像素)
    height: int | None          # 图片高度(像素)
    thumbnail_url: str | None = None  # 缩略图 URL


@dataclass(slots=True)
class SearchPlan:
    """搜索计划：一个分类及其对应的所有搜索关键词。"""

    category: str               # 障碍物分类名称 (如 "parked_bicycle")
    queries: list[str]          # 该分类下的搜索关键词列表


def slugify(value: str, *, max_length: int = 48) -> str:
    """将字符串转换为 URL/文件名安全的简写形式（仅保留字母数字，其余替换为连字符）。"""
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    if not value:
        return "image"
    return value[:max_length].strip("-") or "image"


def dedupe_strings(values: Iterable[str]) -> list[str]:
    """字符串列表去重（大小写不敏感），保持首次出现的顺序，自动跳过空字符串。"""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def normalize_category_map(category_map: dict[str, Iterable[str]]) -> list[SearchPlan]:
    """将原始分类映射规范化为 SearchPlan 列表，对分类名做 slugify 处理并去重关键词。"""
    plans: list[SearchPlan] = []
    for category, queries in category_map.items():
        clean_category = slugify(category, max_length=40)
        clean_queries = dedupe_strings(list(queries))
        if clean_category and clean_queries:
            plans.append(SearchPlan(category=clean_category, queries=clean_queries))
    return plans


def parse_category_spec(raw: str) -> tuple[str, list[str]]:
    """解析命令行分类规格字符串，格式: "category=query1;query2;query3"。"""
    if "=" not in raw:
        raise ValueError("Category spec must use category=query1;query2 format.")
    category, query_blob = raw.split("=", 1)
    queries = [part.strip() for part in query_blob.split(";") if part.strip()]
    if not category.strip() or not queries:
        raise ValueError("Category spec must include a category name and at least one query.")
    return category.strip(), queries


def load_category_config(path: Path) -> dict[str, list[str]]:
    """从 JSON 文件加载分类配置，键为分类名，值为字符串或字符串列表。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Category config must be a JSON object.")
    category_map: dict[str, list[str]] = {}
    for category, queries in payload.items():
        if isinstance(queries, str):
            category_map[category] = [queries]
            continue
        if isinstance(queries, list) and all(isinstance(item, str) for item in queries):
            category_map[category] = queries
            continue
        raise ValueError(f"Invalid query list for category: {category}")
    return category_map


def load_query_file(path: Path, fallback_category: str) -> dict[str, list[str]]:
    """从文本文件加载查询，支持格式：纯查询、category<TAB>query、category|query。"""
    category_map: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 支持 Tab 或 | 分隔分类和关键词
        if "\t" in line:
            category, query = line.split("\t", 1)
        elif "|" in line:
            category, query = line.split("|", 1)
        else:
            category, query = fallback_category, line
        category_map.setdefault(category.strip(), []).append(query.strip())
    return category_map


def load_search_plans(args: argparse.Namespace) -> list[SearchPlan]:
    """综合所有来源（JSON配置/命令行分类/查询文件/关键词参数）构建搜索计划列表。

    优先级：--category-config > --category > --query-file > --query > 默认分类。
    若无任何查询来源，则使用 DEFAULT_CATEGORY_QUERIES。
    """
    category_map: dict[str, list[str]] = {}

    # 1. 从 JSON 配置文件加载
    if args.category_config:
        for category, queries in load_category_config(args.category_config).items():
            category_map.setdefault(category, []).extend(queries)

    # 2. 从命令行 --category 参数加载
    for raw_spec in args.category:
        category, queries = parse_category_spec(raw_spec)
        category_map.setdefault(category, []).extend(queries)

    # 3. 从查询文件加载
    if args.query_file:
        for category, queries in load_query_file(args.query_file, args.custom_category).items():
            category_map.setdefault(category, []).extend(queries)

    # 4. 从命令行 --query 参数加载（归入自定义分类）
    if args.query:
        category_map.setdefault(args.custom_category, []).extend(args.query)

    # 5. 无任何查询时使用默认分类
    if not category_map:
        category_map = {key: list(value) for key, value in DEFAULT_CATEGORY_QUERIES.items()}

    plans = normalize_category_map(category_map)
    if not plans:
        raise ValueError("No valid categories or queries were provided.")
    return plans


def build_session() -> requests.Session:
    """构建带默认请求头的 HTTP 会话。"""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def search_openverse(
    session: requests.Session,
    query: str,
    *,
    wanted: int,
    page_size: int,
) -> list[ImageCandidate]:
    """从 Openverse API 搜索图片候选，分页请求直到获取足够数量。

    自动跳过成人内容和非光栅格式结果。
    """
    candidates: list[ImageCandidate] = []
    page = 1
    while len(candidates) < wanted:
        response = session.get(
            OPENVERSE_URL,
            params={
                "q": query,
                "page": page,
                "page_size": min(page_size, 80),  # Openverse 单页上限 80
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        if not results:
            break
        for item in results:
            if item.get("mature"):  # 跳过成人/敏感内容
                continue
            filetype = str(item.get("filetype") or "").lower().strip(". ")
            if filetype and f".{filetype}" not in RASTER_EXTENSIONS:  # 过滤非光栅格式
                continue
            image_url = item.get("url")
            if not image_url:
                continue
            candidates.append(
                ImageCandidate(
                    provider="openverse",
                    query=query,
                    title=(item.get("title") or "").strip(),
                    image_url=image_url,
                    source_page=item.get("foreign_landing_url"),
                    license=item.get("license"),
                    license_url=item.get("license_url"),
                    creator=item.get("creator"),
                    width=to_int(item.get("width")),
                    height=to_int(item.get("height")),
                    thumbnail_url=item.get("thumbnail"),
                )
            )
            if len(candidates) >= wanted:
                break
        page_count = to_int(payload.get("page_count")) or page
        if page >= page_count:
            break
        page += 1
    return candidates


def search_wikimedia(
    session: requests.Session,
    query: str,
    *,
    wanted: int,
    page_size: int,
    thumbnail_width: int,
) -> list[ImageCandidate]:
    """从 Wikimedia Commons API 搜索图片候选。

    使用 generator=search 分页搜索，请求缩略图和扩展元数据，
    自动跳过非光栅格式的文件。
    """
    candidates: list[ImageCandidate] = []
    offset = 0
    while len(candidates) < wanted:
        response = session.get(
            WIKIMEDIA_URL,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": 6,                # 6 = File 命名空间
                "gsrlimit": min(page_size, 50),    # Wikimedia 单页上限 50
                "gsroffset": offset,
                "prop": "imageinfo",
                "iiprop": "url|size|extmetadata",  # 请求 URL、尺寸和扩展元数据
                "iiurlwidth": thumbnail_width,      # 指定缩略图宽度
                "format": "json",
                "formatversion": 2,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        pages = payload.get("query", {}).get("pages", [])
        if not pages:
            break
        for page in pages:
            if not has_supported_wikimedia_title(page.get("title")):
                continue
            image_info_list = page.get("imageinfo") or []
            if not image_info_list:
                continue
            image_info = image_info_list[0]
            ext_meta = image_info.get("extmetadata") or {}
            # 优先使用缩略图 URL，回退到原图 URL
            image_url = image_info.get("thumburl") or image_info.get("url")
            if not image_url:
                continue
            candidates.append(
                ImageCandidate(
                    provider="wikimedia",
                    query=query,
                    title=(page.get("title") or "").replace("File:", "", 1),
                    image_url=image_url,
                    source_page=image_info.get("descriptionurl"),
                    license=extract_wikimedia_value(ext_meta, "LicenseShortName"),
                    license_url=extract_wikimedia_value(ext_meta, "LicenseUrl"),
                    creator=extract_wikimedia_value(ext_meta, "Artist"),
                    width=to_int(image_info.get("thumbwidth") or image_info.get("width")),
                    height=to_int(image_info.get("thumbheight") or image_info.get("height")),
                    thumbnail_url=image_info.get("thumburl"),
                )
            )
            if len(candidates) >= wanted:
                break
        if len(pages) < min(page_size, 50):  # 已到最后一页
            break
        offset += len(pages)
    return candidates


def extract_wikimedia_value(metadata: dict[str, Any], key: str) -> str | None:
    """从 Wikimedia 扩展元数据中提取纯文本值，去除 HTML 标签并合并多余空白。"""
    raw = metadata.get(key) or {}
    value = raw.get("value")
    if not value:
        return None
    clean = re.sub(r"<[^>]+>", " ", str(value))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or None


def to_int(value: Any) -> int | None:
    """安全地将值转换为整数，失败时返回 None。"""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def has_supported_wikimedia_title(title: Any) -> bool:
    """检查 Wikimedia 文件标题是否具有支持的光栅图片扩展名。"""
    if not isinstance(title, str):
        return False
    extension = Path(title).suffix.lower()
    return extension in RASTER_EXTENSIONS


def discover_candidates(
    session: requests.Session,
    query: str,
    *,
    per_query_limit: int,
    provider_names: set[str],
    page_size: int,
    wikimedia_thumbnail_width: int,
) -> list[ImageCandidate]:
    """从所有指定数据源搜索图片候选，每源请求 3 倍数量以补偿后续过滤。"""
    wanted = max(per_query_limit * 3, per_query_limit)
    candidates: list[ImageCandidate] = []
    if "openverse" in provider_names:
        candidates.extend(
            search_openverse(session, query, wanted=wanted, page_size=page_size)
        )
    if "wikimedia" in provider_names:
        candidates.extend(
            search_wikimedia(
                session,
                query,
                wanted=wanted,
                page_size=page_size,
                thumbnail_width=wikimedia_thumbnail_width,
            )
        )
    return candidates


def pick_extension(url: str, content_type: str | None) -> str | None:
    """根据 URL 和 Content-Type 确定图片扩展名，优先 Content-Type 映射，不支持 SVG。"""
    if content_type:
        content_type = content_type.split(";", 1)[0].strip().lower()
        extension = CONTENT_TYPE_EXTENSIONS.get(content_type)
        if extension:
            return extension
        guessed = mimetypes.guess_extension(content_type)
        if guessed and guessed.lower() in RASTER_EXTENSIONS:
            return guessed.lower()
        if content_type == "image/svg+xml":
            return None
    path = urlparse(url).path.lower()
    extension = Path(path).suffix
    if extension in RASTER_EXTENSIONS:
        return extension
    return None


def download_image(
    session: requests.Session,
    candidate: ImageCandidate,
    *,
    min_width: int,
    min_height: int,
) -> tuple[bytes, str, int, int] | None:
    """下载候选图片并验证格式和尺寸，过滤非光栅/不可读/尺寸过小的图片。"""
    response = session.get(candidate.image_url, timeout=45)
    response.raise_for_status()
    extension = pick_extension(candidate.image_url, response.headers.get("Content-Type"))
    if not extension:
        LOGGER.debug("Skipping non-raster image: %s", candidate.image_url)
        return None

    image_bytes = response.content
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
    except UnidentifiedImageError:
        LOGGER.debug("Skipping unreadable image: %s", candidate.image_url)
        return None

    if width < min_width or height < min_height:
        LOGGER.debug("Skipping small image (%sx%s): %s", width, height, candidate.image_url)
        return None
    return image_bytes, extension, width, height


def save_metadata_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """将元数据行列表保存为 CSV 文件。"""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def ensure_output_dirs(output_dir: Path, category: str) -> Path:
    """确保分类输出目录存在，路径为 output_dir/images/{category_slug}/。"""
    category_dir = output_dir / "images" / slugify(category, max_length=40)
    category_dir.mkdir(parents=True, exist_ok=True)
    return category_dir


def run(args: argparse.Namespace) -> int:
    """主执行流程：按分类搜索 → 下载 → URL/哈希去重 → 保存图片及元数据。

    返回 0 表示成功，1 表示无图片被保存。
    """
    plans = load_search_plans(args)
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    session = build_session()
    provider_names = {name.strip().lower() for name in args.providers.split(",") if name.strip()}
    metadata_rows: list[dict[str, Any]] = []
    manifest_path = output_dir / "metadata.jsonl"
    # URL 去重和内容哈希去重集合（全局共享，跨分类去重）
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    accepted_total = 0
    skipped_total = 0
    category_totals: dict[str, int] = {}

    # 逐分类、逐关键词搜索下载，以 JSONL 格式逐行写入元数据
    with manifest_path.open("w", encoding="utf-8") as manifest:
        for plan_index, plan in enumerate(plans, start=1):
            category = plan.category
            category_dir = ensure_output_dirs(output_dir, category)
            accepted_for_category = 0
            LOGGER.info("[%s/%s] Category: %s", plan_index, len(plans), category)

            for query_index, query in enumerate(plan.queries, start=1):
                # 达到分类上限时停止该分类的所有查询
                if accepted_for_category >= args.limit_per_category:
                    break
                LOGGER.info("  Query [%s/%s]: %s", query_index, len(plan.queries), query)
                accepted_for_query = 0
                remaining_for_category = args.limit_per_category - accepted_for_category
                query_limit = min(args.limit_per_query, remaining_for_category)

                candidates = discover_candidates(
                    session,
                    query,
                    per_query_limit=query_limit,
                    provider_names=provider_names,
                    page_size=args.page_size,
                    wikimedia_thumbnail_width=args.wikimedia_width,
                )
                LOGGER.info("    Found %s candidates before filtering", len(candidates))

                for candidate in candidates:
                    if accepted_for_query >= query_limit:
                        break
                    # URL 去重
                    normalized_url = candidate.image_url.strip()
                    if normalized_url in seen_urls:
                        skipped_total += 1
                        continue
                    seen_urls.add(normalized_url)

                    # 下载并验证图片
                    try:
                        download_result = download_image(
                            session,
                            candidate,
                            min_width=args.min_width,
                            min_height=args.min_height,
                        )
                    except requests.RequestException as exc:
                        LOGGER.warning("    Download failed for %s: %s", candidate.image_url, exc)
                        skipped_total += 1
                        continue

                    if not download_result:
                        skipped_total += 1
                        continue

                    image_bytes, extension, width, height = download_result
                    # SHA-256 哈希去重
                    file_hash = hashlib.sha256(image_bytes).hexdigest()
                    if file_hash in seen_hashes:
                        skipped_total += 1
                        continue
                    seen_hashes.add(file_hash)

                    accepted_total += 1
                    accepted_for_query += 1
                    accepted_for_category += 1
                    category_totals[category] = category_totals.get(category, 0) + 1

                    # 文件名格式：序号_分类简写_关键词简写_哈希前缀.扩展名
                    filename = (
                        f"{accepted_total:05d}_{slugify(category, max_length=18)}_"
                        f"{slugify(query, max_length=22)}_{file_hash[:12]}{extension}"
                    )
                    destination = category_dir / filename
                    destination.write_bytes(image_bytes)

                    metadata = {
                        "file_name": filename,
                        "file_path": str(destination),
                        "category": category,
                        "query": candidate.query,
                        "provider": candidate.provider,
                        "title": candidate.title,
                        "image_url": candidate.image_url,
                        "source_page": candidate.source_page,
                        "license": candidate.license,
                        "license_url": candidate.license_url,
                        "creator": candidate.creator,
                        "width": width,
                        "height": height,
                        "sha256": file_hash,
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    }
                    manifest.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                    metadata_rows.append(metadata)

                LOGGER.info("    Accepted %s images for query", accepted_for_query)

            LOGGER.info("  Accepted %s images for category", accepted_for_category)

    # 写入汇总统计和 CSV 格式元数据
    summary = {
        "categories": [{"category": plan.category, "queries": plan.queries} for plan in plans],
        "providers": sorted(provider_names),
        "accepted_total": accepted_total,
        "accepted_by_category": category_totals,
        "skipped_total": skipped_total,
        "output_dir": str(output_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "limit_per_query": args.limit_per_query,
            "limit_per_category": args.limit_per_category,
            "page_size": args.page_size,
            "min_width": args.min_width,
            "min_height": args.min_height,
            "wikimedia_width": args.wikimedia_width,
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_metadata_rows(output_dir / "metadata.csv", metadata_rows)

    LOGGER.info("Finished. Saved %s images to %s", accepted_total, output_dir)
    if accepted_total == 0:
        LOGGER.error("No images matched the current filters.")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description=(
            "Search and download open-license obstacle images for blind mobility "
            "dataset collection."
        )
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="添加自定义搜索关键词，归入 --custom-category 指定的分类",
    )
    parser.add_argument(
        "--query-file",
        type=Path,
        help="UTF-8 查询文件，每行一个关键词，或 分类<TAB>关键词 / 分类|关键词",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="命令行分类规格，格式: category=query1;query2;query3",
    )
    parser.add_argument(
        "--category-config",
        type=Path,
        help="JSON 分类配置文件，键为分类名，值为字符串或字符串列表",
    )
    parser.add_argument(
        "--custom-category",
        default="custom",
        help="未分类关键词的默认分类名（用于 --query 和 --query-file 无分类行）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("spider/datasets/blind_obstacles"),
        help="图片和元数据的输出目录",
    )
    parser.add_argument(
        "--providers",
        default="openverse,wikimedia",
        help="逗号分隔的数据源列表: openverse,wikimedia",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=12,
        help="每个关键词最多保存的图片数量",
    )
    parser.add_argument(
        "--limit-per-category",
        type=int,
        default=36,
        help="每个分类最多保存的图片数量",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=25,
        help="每次 API 请求返回的搜索结果数",
    )
    parser.add_argument(
        "--min-width",
        type=int,
        default=640,
        help="图片最小宽度(像素)，低于此值跳过",
    )
    parser.add_argument(
        "--min-height",
        type=int,
        default=480,
        help="图片最小高度(像素)，低于此值跳过",
    )
    parser.add_argument(
        "--wikimedia-width",
        type=int,
        default=1600,
        help="Wikimedia 下载图片的优先缩略图宽度(像素)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """程序入口，解析参数并执行主流程。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")
    try:
        return run(args)
    except (requests.RequestException, ValueError) as exc:
        LOGGER.error("Search failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
