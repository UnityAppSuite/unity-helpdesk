"""Shared helper for Unity Helpdesk migration patches.

Wraps a patch body so a failure NEVER aborts the whole `bench migrate`: the
traceback is printed to the deploy terminal AND written to the Error Log, then
swallowed so the next patch still runs. Always prints the elapsed time.

Patch bodies MUST stay idempotent (check-before-create) so that, after the root
cause of a swallowed failure is fixed, simply re-running the patch is safe.
"""

import time

import frappe


def run_patch(name, body):
	"""Run `body()` with deploy-safe error handling. See module docstring."""
	start = time.monotonic()
	try:
		body()
	except Exception:
		tb = frappe.get_traceback()
		# Visible in the `bench migrate` terminal output...
		print(f"\n[unity-patch] {name} FAILED — skipped (migrate continues):\n{tb}\n")
		# ...and persisted to the Error Log for later inspection.
		try:
			frappe.log_error(title=f"unity-patch failed: {name}"[:140], message=tb)
		except Exception:
			# Never let logging itself abort the migrate.
			pass
	finally:
		print(f"[unity-patch] {name} took {time.monotonic() - start:.2f}s")
