"""Keep .agents/skills in sync with .claude/skills, and lint skill frontmatter.

Why this exists (see doc/agent_execution_rules.md sec.11):
  Windows cannot share the two trees with a link. Symbolic links need admin or
  developer mode; Git Bash's `ln -s` returns exit 0 and silently makes a copy;
  a junction is not stored in the repository, so a fresh clone or a worktree
  gets two real directories again; per-file hard links are silently severed by
  editors and by `git checkout`. Every link-based option fails quietly, which
  is the failure mode this project treats as the most expensive one.
  So we keep two real copies and make the drift detectable instead.

  .claude/skills/ is the single source of truth. .agents/skills/ is a mirror
  for other AI tools. Sync direction is always .claude -> .agents.

Also lints the frontmatter, because that is how the drift became harmful:
  two skills used a `## Metadata` heading instead of YAML frontmatter, so their
  trigger description was lost and they stopped auto-invoking. Nothing reported
  an error; they just never fired.

Usage:
    python tools/sync_skills.py --check          # exit 1 on drift or lint error
    python tools/sync_skills.py --apply          # copy .claude -> .agents
    python tools/sync_skills.py --apply --prune  # also delete mirror-only files

Output is ASCII only, on purpose: this script is called from
tools/check_worktrees.ps1, and Windows PowerShell 5.1 renders non-ASCII output
as mojibake under a CP932 console (see doc/agent_execution_rules.md sec.5).
"""

import argparse
import filecmp
import os
import shutil
import sys

SOURCE_REL = os.path.join(".claude", "skills")
MIRROR_REL = os.path.join(".agents", "skills")

# Files that belong to the mirror alone and must not be reported as drift or
# pruned. The mirror's README explains that .claude/skills is the source of
# truth, so it has no counterpart on the source side by design.
MIRROR_ONLY = {"README.md"}


def default_root() -> str:
    """Repository root that contains this script (works inside a worktree too)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def relative_files(base: str) -> set:
    """All files under `base`, as paths relative to it (POSIX separators)."""
    found = set()
    if not os.path.isdir(base):
        return found
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            found.add(os.path.relpath(full, base).replace(os.sep, "/"))
    return found


def lint_frontmatter(source: str) -> list:
    """Every SKILL.md needs YAML frontmatter with name and description.

    A `## Metadata` heading is NOT parsed: the skill keeps working when invoked
    by name but loses its trigger description and stops auto-invoking.
    """
    problems = []
    if not os.path.isdir(source):
        return ["source directory not found: " + source]

    for name in sorted(os.listdir(source)):
        skill_md = os.path.join(source, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        with open(skill_md, "r", encoding="utf-8") as handle:
            head = handle.read(4096)

        lines = head.splitlines()
        if not lines or lines[0].strip() != "---":
            problems.append(
                name + ": SKILL.md does not start with YAML frontmatter ('---'). "
                "A '## Metadata' heading is not parsed and the skill will not auto-invoke."
            )
            continue

        try:
            end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
        except StopIteration:
            problems.append(name + ": frontmatter is not closed by a second '---'.")
            continue

        block = lines[1:end]
        for key in ("name:", "description:"):
            if not any(line.startswith(key) for line in block):
                problems.append(name + ": frontmatter is missing '" + key + "'")
    return problems


def compare(source: str, mirror: str):
    """Return (missing_in_mirror, differing, mirror_only) as sorted lists."""
    src_files = relative_files(source)
    dst_files = relative_files(mirror)

    missing = sorted(src_files - dst_files)
    extra = sorted(dst_files - src_files - MIRROR_ONLY)
    differing = []
    for rel in sorted(src_files & dst_files):
        a = os.path.join(source, rel.replace("/", os.sep))
        b = os.path.join(mirror, rel.replace("/", os.sep))
        # shallow=False: compare contents, not just size and mtime.
        if not filecmp.cmp(a, b, shallow=False):
            differing.append(rel)
    return missing, differing, extra


def apply_sync(source: str, mirror: str, missing, differing, extra, prune: bool) -> None:
    for rel in missing + differing:
        src = os.path.join(source, rel.replace("/", os.sep))
        dst = os.path.join(mirror, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # copyfile preserves bytes exactly, so line endings and encoding are
        # carried over as-is and no BOM can be introduced (sec.5.1).
        shutil.copyfile(src, dst)
        print("  copied  " + rel)

    if extra:
        if prune:
            for rel in extra:
                dst = os.path.join(mirror, rel.replace("/", os.sep))
                os.remove(dst)
                print("  removed " + rel)
            # Drop directories left empty by the removals, so the mirror does not
            # accumulate hollow skill folders (a --check pass would not flag them,
            # since only files are compared).
            for dirpath, dirnames, filenames in os.walk(mirror, topdown=False):
                if dirpath == mirror or dirnames or filenames:
                    continue
                os.rmdir(dirpath)
                print("  removed " + os.path.relpath(dirpath, mirror).replace(os.sep, "/") + "/")
        else:
            print("  NOTE: " + str(len(extra)) + " mirror-only file(s) left untouched "
                  "(use --prune to delete them)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync and lint the skills mirror.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="report drift and lint errors; exit 1 if any")
    mode.add_argument("--apply", action="store_true",
                      help="copy .claude/skills -> .agents/skills")
    parser.add_argument("--prune", action="store_true",
                        help="with --apply, delete files that exist only in the mirror")
    parser.add_argument("--root", default=None,
                        help="repository root (default: the checkout containing this script)")
    args = parser.parse_args()

    root = os.path.abspath(args.root) if args.root else default_root()
    source = os.path.join(root, SOURCE_REL)
    mirror = os.path.join(root, MIRROR_REL)

    problems = lint_frontmatter(source)
    missing, differing, extra = compare(source, mirror)

    if args.apply:
        if missing or differing or (extra and args.prune):
            print("Syncing .claude/skills -> .agents/skills")
            apply_sync(source, mirror, missing, differing, extra, args.prune)
        else:
            print("Skills mirror already in sync.")
        if problems:
            print("")
            print("Frontmatter problems remain (NOT fixed by --apply):")
            for problem in problems:
                print("  [!] " + problem)
            return 1
        return 0

    # --check
    failed = False
    if problems:
        failed = True
        print("[!] Skill frontmatter problems:")
        for problem in problems:
            print("    " + problem)
    if missing or differing or extra:
        failed = True
        print("[!] Skills mirror out of sync (.claude/skills -> .agents/skills):")
        for rel in missing:
            print("    missing in .agents : " + rel)
        for rel in differing:
            print("    content differs    : " + rel)
        for rel in extra:
            print("    only in .agents    : " + rel)
        print("    Fix: python tools/sync_skills.py --apply")

    if failed:
        return 1
    print("Skills mirror in sync; frontmatter OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
