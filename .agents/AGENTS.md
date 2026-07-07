# Release and Versioning Protocol

Whenever you make a change, commit code, and push to GitHub, you MUST ALWAYS follow this exact sequence:
1. **Update the `manifest.json` version** to the next appropriate semver tag (e.g. `v0.1.0-alpha.7`).
2. **Update the `README.md`** to add notes about the new version and what has been fixed or changed.
3. **Commit and Tag** the changes using Git (e.g., `git commit -m "..."`, `git tag v...`).
4. **Push the commit and the tags** (`git push origin master --tags`).
5. **Create a GitHub Release** using the GitHub REST API and PowerShell. You MUST fetch the Personal Access Token via `git credential fill` and then use `Invoke-RestMethod` to publish the release with detailed release notes. This ensures HACS picks up the new version correctly instead of showing the commit hash.

Example script for creating the release:
```powershell
$token = "protocol=https`nhost=github.com" | git credential fill | Select-String -Pattern "password=(.+)" | % { $_.Matches.Groups[1].Value }
$headers = @{ "Authorization" = "token $token"; "Accept" = "application/vnd.github.v3+json" }
$body = '{"tag_name": "vX.X.X", "name": "vX.X.X - Summary", "body": "Release Notes", "draft": false, "prerelease": true}'
Invoke-RestMethod -Uri "https://api.github.com/repos/mantovanellimatteo/MyHOME/releases" -Method Post -Headers $headers -Body $body -ContentType "application/json"
```
