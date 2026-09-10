# ADV SWE Auto Attendance

## Commands

Run commands from the project folder:

```bash
cd /Users/bytedance/Desktop/adv_swe_auto_attendance
```

## Test Command

Use this to test the flow with the always-present attendance records item:

```bash
.venv/bin/python adv_swe_attendance.py attendance-records
```

This command logs in, opens Courses, opens the target module, expands the attendance folder, and clicks the attendance records item.

## Default Command

Use this for the real auto-attendance run:

```bash
.venv/bin/python adv_swe_attendance.py
```

This is the same as:

```bash
.venv/bin/python adv_swe_attendance.py watch
```

The default command waits for the configured attendance window in `.env`, then keeps refreshing until it finds an active item like `Tutorial 1 attendance`, `Tutorial 2 attendance`, or `Tutorial 3 attendance`.

