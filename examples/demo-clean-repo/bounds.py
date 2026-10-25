def get(items, index):
    if index < 0 or index >= len(items):
        return None
    return items[index]
