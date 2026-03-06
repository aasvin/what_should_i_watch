# This is the original instruction file that was given to Claude to bootstrap the scraping process. The finalized instruction set is at AGENT_INSTRUCTIONS.md


#Find Me Something To Watch

This file contains the core idea for a software product that will help a customer find something to watch across the various streaming platforms that they have a subscription to. The list currently includes 

1) Netflix
2) Amazon Prime Video 




##Instructions for Agents
If you are a software agent, then as a first step you will build a plan for creating the software product. Requirements for the software product are 

Inputs: 
- The customer will be logged into their Amazon Prime Video account using their Chrome browser on their MacOS device. 
- You should assume that you have full control over their browser to read content. 


Outputs:
- Produce a file containing all of the content that you see on the logged in browser screen in json format. The file should contain the name of the movie or show and the duration. 



#Suggested Steps
1) You should ask the customer if they can take control of the browser. You are essentially playing the role of the customer who is navigating the screen, scrolling down, moving from the Movies tab to the TV Shows tab to see what content is available to watch. Your code should be able to take control of the browser and navigate to Movies and TV Shows sections of Prime Video within the browser. Your code should scroll down so that more content can be generated. 

2) You can then take a dump of the content that you see on the browser video. Generate a file in json format. Your fields should contain the Name, Duration of the content and whether its a TV show or a Movie. 


#Instructions for Human Developers

