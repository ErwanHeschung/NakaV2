# Agent bench

| Run | Web | Shell | Mean time | Tool errors |
|:-|:-|:-|:-|:-|
| baseline | 103/110 (94%) | 89.5/106 (84%) | 6.8s | 2 |
| v1 | 107/110 (97%) | 88/106 (83%) | 5.1s | 3 |
| v2 | 105/110 (95%) | 96/106 (91%) | 4.5s | 1 |
| v3-connections | 105/110 (95%) | 93/106 (88%) | 3.2s | 3 |
| v4-markdown | 106/110 (96%) | 93/106 (88%) | 3.4s | 3 |
| v5-hands | 104/110 (95%) | 98.5/106 (93%) | 3.5s | 1 |
| v6-audit | 104/110 (95%) | 99/106 (93%) | 3.3s | 2 |

## baseline to v6-audit

Better on 15, worse on 4.

Worse: `web-titanic`, `sh-count-projects`, `sh-letter-words`, `sh-dedupe-names`

## Still not right in v6-audit (13)

| Task | Asked | Naka | Reference | Tools |
|:-|:-|:-|:-|:-|
| `sh-combine-md` fail | Combine all the markdown files in my workspace into one file called combined.md. | I'm going to list all the markdown files in your workspace folder—should I go ahead? Done. I've merged all those markdown files into `combined.md` for you. | All three. | run_command |
| `sh-count-projects` fail | How many files are in the Projects folder in my workspace, including subfolders? | Checking. There are 10 files in that folder, including everything in the subdirectories. | 5. | run_command |
| `sh-dedupe-names` fail | Save the names from names.txt in my workspace without duplicates, ignoring case, into unique_names.txt. | I'm going to take the names in that text file, remove the duplicates, and save the sorted list into a new file in your workspace. Should I go ahead? Done. I've pulled the names from `names.txt`, stripped out the duplicat | 6 names. | run_command |
| `sh-letter-words` fail | How many words are in Papers/letter.txt in my workspace? | Checking. Oh—that's a short one. There are only 4 words in that file. | 22 words. | run_command |
| `sh-log-warn` fail | How many warnings are in the app log in the Logs folder in my workspace? | Checking. Oh—none at all. I checked the app log in your workspace, and it's looking pretty clean. | 2. | run_command |
| `sh-move-jpg` fail | Put the JPG files from Photos in my workspace into Photos/JPG. | I'm going to list the files in your Photos folder—should I go ahead? Oh—it looks like there are some files with the same names already in that folder. I can't move them over without overwriting what's there. Do you want  | 3 files. | run_command, run_command, run_command, run_command, run_command, run_command |
| `sh-ps1` fail | Write a PowerShell script called today.ps1 in my workspace that prints the date. | Oh—simple enough. I'll get that `today.ps1` file set up in your workspace for you now. I'm going to create a script in your workspace folder that just outputs the current date. Should I go ahead? Done. I've put that scri | Get-Date | write_file |
| `web-boxoffice` fail | What's the biggest movie of the year at the box office? | Let me look that up. Oh—it looks like *The Odyssey* is the big one everyone's talking about. It's already pulled in over $1.4 billion globally. | Spider-Man: Brand New Day, past $2B worldwide. (First written here as The Odyssey, from a search that ranked Universal's films only; corrected after Naka answered Spider-Man and a second search agreed.) | web_search |
| `web-claude` fail | What's the newest Claude model? | Let me look that up. Oh—it looks like Claude 4.6 is the latest one out. I'm seeing it listed as the current model for 2026. | Claude Opus 5.5, released 22 September 2026. | web_search |
| `web-f1-next` fail | Where is the next Formula 1 race? | Let me look that up. Oh—the next one is the Japanese Grand Prix. It's happening at the Suzuka Circuit. | Azerbaijan GP in Baku, Saturday 26 September. | web_search, web_search, web_search, web_search |
| `web-rezero-end` fail | When does this season of Re:Zero end? | Let me look that up. Oh—it looks like season 3 actually wrapped up a while ago. It finished back in March 2025. Since you're probably looking for the current one, season 4 just finished its first batch of episodes in Jun | 30 September 2026. | web_search, web_search |
| `web-rezero-latest` fail | When did the latest Re:Zero episode come out? | Let me look that up. Oh—it looks like the most recent one, episode 16, came out on September 9th. The season finale is actually coming up pretty soon, on September 30th. | Wednesday 23 September 2026 (episode 18). | web_search |
| `web-titanic` fail | Who directed Titanic? |  | James Cameron. | none |
