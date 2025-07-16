Welcome to StrokeGPT v1.1 - Manual Setup!

This version lets you run StrokeGPT yourself from the code, so you're in charge.
--------------------
QUICK START GUIDE (Manual Setup)
--------------------

**Part 1: Get Ready (One-Time Setup - harder than it looks, I promise)**

1.  **INSTALL PYTHON:**
    * Go to: https://www.python.org/downloads/
    * Get and install the **NEWEST Python 3**.
    * **SUPER IMPORTANT:** When installing, make sure you **TICK THE BOX** that says "Add Python to PATH" (or something similar).
    This is a must-do for the next steps.

2.  **INSTALL OLLAMA (The AI Helper):**
    * Go to: https://ollama.com
    * Download and install Ollama.
    This program runs the AI right on your computer.

3.  **UNZIP THIS PROJECT:**
    * Take all the files from this .zip and put them into a new, empty folder on your computer (like C:\StrokeGPT).
    Don't forget where you put it!

**Part 2: Run StrokeGPT!**

1.  **OPEN YOUR TERMINAL (Command Prompt/PowerShell):**
    * **On Windows:** Find that folder where you unzipped "StrokeGPT".
    * Click on the **address bar** at the top of the folder window (it shows the folder path, like `C:\Users\YourName\StrokeGPT`).
    * Type `cmd` (for Command Prompt) or `powershell` (for PowerShell) there and hit `Enter`.
    * A black (or blue) window will pop up. That's your terminal.
2.  **INSTALL PYTHON PARTS (Only once):**
    * In that terminal window, type this command **EXACTLY** and press `Enter`:

        pip install -r requirements.txt

    * *What happens:* You'll see text fly by as Python grabs and puts in all the stuff it needs.
    This could take a couple of minutes.
    * *If you get errors:* Check your internet connection.
    If it complains "Python not found", you messed up step 1, reinstall Python and make sure "Add Python to PATH" was checked.
3.  **DOWNLOAD THE AI MODEL (Only once):**
    * **First, make sure the Ollama app is RUNNING.** Look for its little icon near the clock on your screen (system tray).
    If it's not there, open Ollama from your Start Menu.
    * Once Ollama is going, in the **same terminal window**, type this command **EXACTLY** and press `Enter`:

       ollama pull llama3:8b-instruct-q4_K_M

    * *What happens:* Ollama will download the AI brain.
    This might take a while, depending on your internet. You'll see a progress bar.
4.  **START STROKEGPT SERVER:**
    * In the **same terminal window**, type this command **EXACTLY** and press `Enter`:

        python app.py

    * *What happens:* The server will start up, and you'll see messages like "Server starting..." and an address like `http://127.0.0.1:5000`.
    This window **HAS to stay open** while you're using StrokeGPT.

5.  **OPEN YOUR BROWSER:**
    * Open your web browser (like Chrome, Firefox, or Edge) and go to this address:
        ```
        [http://127.0.0.1:5000](http://127.0.0.1:5000)
        ```

--------------------
HOW TO STOP THE APP
--------------------

* When you're done playing, go back to that terminal window where `app.py` is running.
* Press `Ctrl` + `C` on your keyboard (hold Ctrl, then press C).
* It might ask "Terminate batch job (Y/N)?". Type `Y` and hit `Enter`.
* The server will shut down and automatically save your chat memories and any new moves it learned.
Then you can close the terminal window.

--------------------
QUICK TROUBLESHOOTING
--------------------

* **"Python command not found"**: Reinstall Python and make sure you checked "Add Python to PATH".
* **"Ollama command not found"**: Did you install Ollama?
* **"The AI is responding slowly."**
    * This is all about your computer's graphics card (GPU).
    A beefier card is the only way to make it faster.
* **"The AI seems 'dumb' or doesn't follow instructions well."**
    * Make sure Ollama is running and has completely downloaded the `mistral-openorca` model.
* **"The app crashed or is frozen."**
    * Just go back to the black server window, close it (or hit `Ctrl+C`), then try steps 4 and 5 again.
For more details and advanced fixes, check out the `MANUAL.txt` file.