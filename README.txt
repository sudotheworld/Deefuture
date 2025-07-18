# StrokeGPT Handy Controller

Welcome to StrokeGPT! This is a guide to help you set up your own private, voice-enabled AI companion for The Handy.

## What Does It Do?

- **An AI That Controls Your Handy**: Chat with an AI, and it will control The Handy's movements in real-time based on the conversation.
- **Realistic Voice**: The AI's responses are spoken aloud in a natural voice, complete with realistic sounds like moans, gasps, and sighs.
- **Made For You**: A simple setup guide helps you calibrate The Handy to your own body, so all movements are perfectly comfortable.
- **It Remembers You**: The AI can learn your preferences and remember details from past chats, creating a more personal experience.
- **Completely Private**: Everything runs on your own computer. Your conversations and settings are never sent to anyone.
- **Built-in Safety**: A "speed governor" is always active to ensure the movements stay within a safe and comfortable range.

---

## How to Get Started (Step-by-Step Guide)

Follow these steps carefully, and you'll be up and running in no time.

### Step 1: Get the Necessary Software

Before we begin, you need three main things on your computer.

1.  **Python**: This is the programming language the app is built on.
    - Go to the [official Python website](https://www.python.org/downloads/) and download the latest version for your operating system (Windows or Mac).
    - When installing, **make sure to check the box that says "Add Python to PATH"**. This is very important!

2.  **Ollama (for the AI's Brain)**: This is a free program that runs the AI model on your computer.
    - Go to the [Ollama website](https://ollama.com/) and download it.
    - After installing, open your computer's terminal (Command Prompt on Windows, or Terminal on Mac) and run the following command to download the specific AI model we'll be using:
      
      ollama run llama3:8b-instruct-q4_K_M
      
    - This will take some time as it downloads the model (several gigabytes). Once it's done, you can close the terminal. Ollama will keep running in the background.

3.  **The Project Files (StrokeGPT)**:
    - Go to the GitHub page for this project.
    - Click the green `<> Code` button.
    - In the dropdown menu, click **"Download ZIP"**.
    - Unzip the downloaded file into a folder where you can easily find it, for example, on your Desktop.

### Step 2: Set Up the Project Folder

Now we'll get the StrokeGPT folder ready.

1.  **Create the `requirements.txt` File**: (OR IF IT ALREADY EXISTS, SKIP THIS STEP!)
    - Inside your unzipped project folder, create a new text file.
    - Name it `requirements.txt`.
    - Open the file and paste the following three lines into it. Make sure there are no extra spaces.
      ```
      flask
      requests
      elevenlabs
      ```
    - Save and close the file.

2.  **Add the Splash Screen Image**:
    - Inside the project folder, create a new folder and name it `static`.
    - **Add the `splash.jpg into there.`**.

### Step 3: Install the Helper Programs

This step uses the `requirements.txt` file you just created to install the programs StrokeGPT needs to function.

1.  **Open a Terminal in Your Project Folder**:
    - **On Windows**: Go into your project folder in File Explorer. Click on the address bar at the top, type `cmd`, and press Enter. A command prompt will open directly in that folder.
    - **On Mac**: Open the Terminal app. Type `cd ` (with a space after it), then drag your project folder from Finder and drop it onto the Terminal window. Press Enter.

2.  **Run the Install Command**:
    - With the terminal open in your project folder, type the following command and press Enter:
    
      pip install -r requirements.txt
   
    - This will automatically install Flask, Requests, and ElevenLabs.

### Step 4: Run the App!

You're all set! To start the StrokeGPT server:

1.  **Run the Main Script**:
    - In the same terminal window, type the following command and press Enter:
    
      python app.py
 
    - You'll see some text appear, ending with a line that says `Running on http://127.0.0.1:5000`. This means the server is working! Keep this terminal window open.

2.  **Open the App in Your Browser**:
    - Open your web browser (like Chrome or Firefox).
    - Go to this address: `http://127.0.0.1:5000`
    - The splash screen should appear. Press Enter to begin the on-screen setup guide. Enjoy!
