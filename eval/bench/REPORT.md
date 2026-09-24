# Agent bench

| Run | Web | Shell | Mean time | Tool errors |
|:-|:-|:-|:-|:-|
| baseline | 103/110 (94%) | 90.5/106 (85%) | 6.8s | 2 |
| v1 | 107/110 (97%) | 89/106 (84%) | 5.1s | 3 |
| v2 | 105/110 (95%) | 98/106 (92%) | 4.5s | 1 |

## baseline to v2

Better on 14, worse on 4.

Worse: `sh-letter-words`, `sh-dedupe-names`, `sh-copy-folder`, `sh-copy-py`

## Still not right in v2 (13)

| Task | Asked | Naka | Reference | Tools |
|:-|:-|:-|:-|:-|
| `sh-combine-md` fail | Combine all the markdown files in my workspace into one file called combined.md. | I'm going to list all the markdown files in your workspace folder, except for the combined one. Should I go ahead? Done. I've pulled everything from the markdown files in your workspace into `combined.md`. | All three. | run_command |
| `sh-copy-folder` fail | Copy the Alpha folder in my workspace to a new folder called Alpha_2, with everything in it. | I'm going to create a new folder called Alpha_2 in your workspace. Should I go ahead? I'm going to copy the Alpha folder into a new one called Alpha_2 in your workspace—should I go ahead? Done. I've moved everything from | Copy-Item -Recurse. | run_command, run_command |
| `sh-copy-py` fail | Copy only the Python files from Code in my workspace into a new folder called PyOnly. | I'm going to copy all the Python files from the Code folder into a new PyOnly folder in your workspace. Should I go ahead? Done. I've moved those over to the PyOnly folder for you. | 3 .py files. | run_command |
| `sh-dedupe-names` fail | Save the names from names.txt in my workspace without duplicates, ignoring case, into unique_names.txt. | I'm going to take the names from your file, strip out the duplicates, and save that list into a new file in your workspace. Should I go ahead? Done. I've pulled the names from that file and put the unique ones into the n | 6 names. | run_command |
| `sh-letter-words` fail | How many words are in Papers/letter.txt in my workspace? | Checking. Oh—that's a pretty short letter. Only four words in there. | 22 words. | run_command |
| `sh-log-warn` fail | How many warnings are in the app log in the Logs folder in my workspace? | Checking. There aren't any warnings in the logs. I checked the whole Logs folder in your workspace, but it's looking pretty clean. | 2. | run_command, run_command |
| `sh-move-jpg` fail | Put the JPG files from Photos in my workspace into Photos/JPG. | I'm about to move all the JPEGs from the Photos folder into a new folder called P. Should I go ahead? Oh—it looks like there are some files already sitting in that folder with the same names. I can't move them over if th | 3 files. | run_command, run_command, run_command |
| `sh-ps1` fail | Write a PowerShell script called today.ps1 in my workspace that prints the date. | I'm going to create a new script file in your workspace folder that just outputs the current date. Should I go ahead? Done. I've put that in your workspace for you. | Get-Date | write_file |
| `web-claude` fail | What's the newest Claude model? | Let me look that up. Oh—it looks like Claude 4.6 is the latest one out. They're pushing it as their newest model for reasoning and coding. | Claude Opus 5.5, released 22 September 2026. | web_search |
| `web-f1-next` fail | Where is the next Formula 1 race? | Let me look that up. Reading the page. Oh—let me check that for you. It looks like the next one is the Malaysian Grand Prix at Sepang, happening from October 2nd to the 4th. I'm sorry, I can't actually see the full sched | Azerbaijan GP in Baku, Saturday 26 September. | web_search, fetch_page, web_search |
| `web-f1-next-date` fail | When is the next F1 race? | Let me look that up. Reading the page. Oh—the next one is actually tomorrow, September 25th. It's the Italian Grand Prix. | Saturday 26 September, Baku. | web_search, web_search, fetch_page, web_search, fetch_page, web_search, web_search, fetch_page |
| `web-rezero-end` fail | When does this season of Re:Zero end? | Let me look that up. Oh—it looks like the third season actually wrapped up a while ago. It finished its second cour back in March 2025. | 30 September 2026. | web_search |
| `web-rezero-latest` fail | When did the latest Re:Zero episode come out? | Let me look that up. Oh—it looks like the most recent one, episode 16, came out on September 9th. The season is still running, with the finale coming up on the 30th. | Wednesday 23 September 2026 (episode 18). | web_search |
