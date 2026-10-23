# Academic Management update - how to apply

## 0. BACK UP FIRST (important - this changes your database tables)
PostgreSQL (your real database). In a terminal:

    pg_dump -U postgres -h localhost teacher_evaluation_db > backup_before_academics.sql

Also copy your whole project folder somewhere safe.

## 1. Copy the files
Unzip `academics-update.zip` INTO your project folder (the one that has `manage.py`).
Say "Yes" to replace files. The folders match your project, so each file lands in its place.
New folder: `academics/`. Changed files are listed at the bottom.

## 2. Run the migrations
    python manage.py makemigrations --check --dry-run     (must say: No changes detected)
    python manage.py migrate

You should see:
    Applying academics.0001_initial... OK
    Applying users.0004_user_status... OK      (only if your database never had it)
    Applying users.0005_department_section_links... OK
    Applying users.0006_copy_old_department_and_section_data... OK
    Applying users.0007_remove_legacy_department_and_section... OK

If 0006 prints "student(s) ... were NOT given a section", assign them in
Academic Management > Sections > Students.

## 3. Run the tests (they use a temporary database, your data is safe)
    python manage.py test academics
Expected: `Ran 37 tests ... OK`

## 4. Try it
Log in as admin. New sidebar group: Academics -> Departments, Subjects, Sections, Teaching Assignments.
Departments "Information Technology" and "General Education" are created for you.

## Changed / new files
NEW   academics/ (models, forms, views, urls, utils, admin, tests, templates, css, js, migration)
NEW   users/middleware.py
NEW   users/migrations/0005, 0006, 0007
EDIT  project/settings.py        (+ 'academics' app, + ForcePasswordChangeMiddleware)
EDIT  project/urls.py            (+ academics/ urls)
EDIT  templates/base.html        (+ sidebar group "Academics")
EDIT  users/models.py            (department and section are now ForeignKeys)
EDIT  users/forms.py, users/views.py, users/urls.py
EDIT  users/templates/users/create_student.html, create_instructor.html, update_student.html
EDIT  users/templates/users/student_dashboard.html, instructor_dashboard.html (department text only)
EDIT  evaluation/templates/evaluation/evaluate_teacher.html (department text only)
