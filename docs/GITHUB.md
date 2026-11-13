# Push to GitHub

GitHub stores your code safely and enables backups and the automatic tests included in this project.

**The one rule: never upload your keys.** The included `.gitignore` already excludes `.env` and the whole `data/` folder (which holds saved keys, your access token, the database and videos). Check before you push.

## One-time setup

1. Install Git: https://git-scm.com/downloads
2. Create a free account at https://github.com
3. Tell Git who you are:
   ```
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

## Create the repository

On GitHub click **New repository**. Name it `lumina-studio`. Choose **Private** (recommended). Do **not** tick "Add a README". Click **Create repository**.

## Push your project

In the VS Code terminal, inside the project folder:

```
git init
git add .
git status
```

**Stop and read the `git status` list.** It must NOT contain `.env`, anything inside `data/`, or any `.db` file. If it does, run `git rm -r --cached data .env` and check `.gitignore`.

Then:

```
git commit -m "Initial commit: Lumina Studio"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/lumina-studio.git
git push -u origin main
```
GitHub asks you to sign in the first time (a browser window opens).

## Later changes

```
git add .
git commit -m "Describe what you changed"
git push
```

## What the included automation does

- `.github/workflows/tests.yml` runs all tests on every push (on GitHub's computers) and scans the dependencies for known security problems with `pip-audit`.
- `.github/dependabot.yml` opens a pull request weekly when a dependency has an update.

## If a key ever leaks

Assume it is public. Immediately **delete or regenerate the key** in the provider's dashboard (Gemini, OpenRouter, Pexels, ...), enter the new one in Settings, then remove it from the repository history. Deleting the file in a later commit is **not** enough because the history keeps it.

## Sharing your music or logo

`assets/music/*` and `assets/brand/logo.png` are ignored by default because their licences may not allow sharing. Remove those lines from `.gitignore` only if you own the rights.
