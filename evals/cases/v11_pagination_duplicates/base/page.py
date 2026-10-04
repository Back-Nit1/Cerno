def page(items: list, page_number: int, size: int) -> list:
    start = page_number * size
    return items[start : start + size]
