"""서버에 저장된 길찾기 프로그램의 결정론 실행. 클라이언트가 도착 여부를 결정하지 않는다."""

from __future__ import annotations

HEADINGS = ("up", "right", "down", "left")
DELTA = ((-1, 0), (0, 1), (1, 0), (0, -1))


def run(program: list[dict], board: dict) -> dict:
    cell, heading = board["start"], HEADINGS.index(board["heading"])
    cells, outcome, steps = [cell], "ended", 0
    stopped = False

    def neighbor(direction):
        row, col = divmod(cell, 5)
        dr, dc = DELTA[direction % 4]
        row, col = row + dr, col + dc
        return row * 5 + col if 0 <= row < 5 and 0 <= col < 5 else None

    def is_open(direction):
        target = neighbor(direction)
        return target is not None and target not in board["puddles"]

    def execute(node):
        nonlocal cell, heading, steps, outcome, stopped
        if stopped:
            return
        steps += 1
        if steps > 60:
            outcome, stopped = "tooLong", True
            return
        op = node["op"]
        if op == "turn":
            heading = (heading + (-1 if node.get("dir") == "left" else 1)) % 4
        elif op == "stop":
            stopped = True
        elif op == "move":
            for _ in range(25 if node.get("until") == "blocked" else min(node.get("count") or 1, 25)):
                if node.get("until") == "blocked" and not is_open(heading):
                    break
                target = neighbor(heading)
                if target is None:
                    outcome, stopped = "bumped", True
                    return
                cell = target
                cells.append(cell)
                if cell in board["puddles"]:
                    outcome, stopped = "splashed", True
                    return
                if cell == board["goal"]:
                    outcome, stopped = "arrived", True
                    return
        elif op == "if":
            direction = heading + {"front": 0, "left": -1, "right": 1}.get(node.get("sensor"), 0)
            condition = is_open(direction) == (node.get("state") == "open")
            for child in node.get("then" if condition else "else", []):
                execute(child)
        elif op == "repeat":
            seen = set()
            while not stopped:
                state = (cell, heading)
                if state in seen:
                    outcome, stopped = "loop", True
                    return
                seen.add(state)
                for child in node.get("body", []):
                    execute(child)

    for node in program:
        execute(node)
    return {"cell": cell, "heading": HEADINGS[heading], "cells": cells, "outcome": outcome}
