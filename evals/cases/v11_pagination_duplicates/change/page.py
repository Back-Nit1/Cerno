def page(items: list, page_number: int, size: int) -> list:
    start = max(0, page_number - 1) * size
    return items[start : start + size]
