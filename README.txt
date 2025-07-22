StrokeGPT Handy Controller

Welcome to StrokeGPT! This is a simple guide to help you set up your own private, voice-enabled AI companion for The Handy.

What Does It Do?

    AI-Controlled Fun: Chat with an AI that controls your Handy's movements in real-time.

Realistic Voice: The AI can speak its replies with a natural voice.

Personalized for You: A simple on-screen guide helps you calibrate the device to your body for a perfect fit.

It Remembers You: The AI learns your preferences and remembers details from past chats.

100% Private: Everything runs locally on your computer. Nothing is ever sent over the internet.

Built-in Safety: The app includes safety limiters to ensure movements always stay within your comfortable range.

How to Get Started (easier than it looks!)

Step 1: Install Prerequisites

You need two free programs to run the app.

    Python:

        Download the latest version from the 

        official Python website.

During installation, you 

must check the box that says "Add Python to PATH".

Ollama (The AI's "Brain"):

    Download Ollama from the 

    Ollama website.

After installing, open a terminal (Command Prompt on Windows, Terminal on Mac) and run the following command 

once to download the AI model:

ollama run llama3:8b-instruct-q4_K_M

This will take a few minutes. Once it's finished, you can close the terminal. 

Make sure the Ollama application is running in the background before you start StrokeGPT.

Step 2: Download & Install StrokeGPT

    Download the Project:

        Go to the project's GitHub page and click the green 

        <> Code button, then select "Download ZIP".

Unzip the file into a folder you can easily access, like your Desktop.

Install Required Libraries:

    Open a terminal directly in your new project folder:

        Windows: Open the folder, click the address bar at the top, type cmd, and press Enter.

Mac: Open the Terminal app, type cd , then drag the project folder from Finder into the terminal window and press Enter.

        In the terminal, run this command:

        pip install -r requirements.txt

Step 3: Run the App!

    Start the Server:

        In the same terminal (still in your project folder), run this command:

python app.py

The server is working when you see a message ending in 

Running on http://127.0.0.1:5000. 

Keep this terminal window open.

Open in Browser:

    Open your web browser and go to the following address:


http://127.0.0.1:5000

The splash screen will appear. Press Enter to begin the on-screen setup guide. Enjoy!