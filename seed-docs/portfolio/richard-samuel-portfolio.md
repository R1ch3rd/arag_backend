# Richard Samuel — AI Engineer

Richard Samuel is an AI Engineer based in Bangalore, India. He works at MeshDefend,
an AI-powered IT services company for data infrastructure, where he is currently
full-time after a six-month internship from December to May. He builds agentic AI
systems and production ML infrastructure.

Education: B.E. in Computer Science with an AI & ML specialization from PSG College
of Technology.

Contact: richard.samuel.rsd@gmail.com
GitHub: github.com/R1ch3rd
LinkedIn: linkedin.com/in/richard-samuel-d
Portfolio: r1ch3rd.github.io/folio

## Leadership, activities and awards

At PSG Tech, Richard was Events & Sponsorship Head (2023-24) and Technical Team
Member (2024-25) of The Eye, a student club. He is a Student Member of the IEEE
Geoscience and Remote Sensing Society. He received the Technical Excellence Award
from the Computer Science Engineering Association, PSG Tech (April 2026), and placed
3rd at Hacksphere (2025), 2nd (2025) and 3rd (2024) at Ideathon, with a special
mention at the Caterpillar Codeathon (2024). At MeshDefend he shipped six automation
workflows to production and contributed to a system-design feature with a
provisional patent filed.

## Research and Publications

### Enhanced Multi-Scale Pyramid Deep Image Prior for Unsupervised Remote Sensing Image Restoration
Published in IEEE Geoscience and Remote Sensing Letters (GRSL), 2026 (Early Access
on IEEE Xplore), DOI 10.1109/LGRS.2026.3735466. Unsupervised restoration of
satellite imagery using a multi-scale pyramid deep image prior, removing the need
for paired clean/corrupted training data. Developed during a research internship at
ISRO/NRSC (Indian Space Research Organisation / National Remote Sensing Centre).
Code: github.com/R1ch3rd/EMSP-DIP

### FedHyperGNN: Temporal Hypergraph Neural Networks for Privacy-Preserving Federated Recommendation Systems
Accepted and presented at IEEE NMITCON 2026 (Bengaluru, September 2026). A federated learning approach using hypergraph neural
networks to model higher-order user-item relationships in recommendation systems,
without centralizing user data. Includes differential privacy guarantees with a
configurable privacy budget.

### Reinforcement Learning Based Framework for Dynamic Defense in Adversarial Environments
Won the Best Paper Award at Research Conclave 2026, PSG College of Technology. A PPO-based
reinforcement learning framework for defending medical imaging classifiers against
adversarial perturbations. The RL agent selects image preprocessing and denoising
actions at inference time to restore classifier accuracy on perturbed fetal brain
ultrasound images, defending an EfficientNet-B0 classifier against attacks
including PGD, BIM, R+FGSM, DeepFool, and Carlini-Wagner.

### CAT-SR: Cross-Market Attention Transfer for Seller Recommendations
Presented at ICCT-SD 2026, the International Conference on Intelligent
Computational and Communication Technologies for Sustainable Development.

## Projects

### aRAG (this system)
A serverless retrieval-augmented generation platform. Users upload documents and
converse with them. Architecture: AWS Lambda functions behind API Gateway, Cognito
authentication, Pinecone vector search with Gemini embeddings, DynamoDB for
sessions/messages/documents metadata, S3 for document storage, and Upstash Redis
for caching and rate limiting. The chat you are using right now runs on aRAG's
public guest mode.

### FinAL
AI-powered stock analysis platform: LSTM price forecasting in PyTorch, BERT
sentiment analysis on financial news, and Gemini-driven insights layered over live
market data from Finnhub and yfinance. FastAPI backend, React frontend.

### PixelPerfect
AI image transformation suite built at a hackathon: ESRGAN super-resolution
upscaling, Stable Diffusion image generation, and Gemini-powered captioning and
summaries. FastAPI backend, React frontend, Firebase storage.

### SureScan
Brain tumor detection and diagnosis assistant: YOLOv11 localization plus a
classifier ensemble with XGBoost, and an AI chat interface for interpreting scan
results. React frontend.

### Misinformation Detection Agent (ReZero)
Agentic fact-checking service: detects AI-generated text and images with
transformer models, then verifies claims with an LLM agent over the Tavily search
API. FastAPI backend.

## Interests outside work

Tennis (a regular player; the portfolio includes a small playable tennis game),
photography (selected work at vsco.co/richychrich86/gallery), console gaming,
LEGO, and robotics. Embodied agents are a long-term research
interest.

## Frequently asked

What is Richard looking for? Conversations about agentic AI systems, applied ML,
and research collaboration. Reach out at richard.samuel.rsd@gmail.com.

What stack does he work with? Python, PyTorch, LangGraph, MCP servers, AWS
serverless (Lambda, DynamoDB, Cognito, API Gateway), FastAPI, React/TypeScript,
Pinecone, and the Gemini API, among others.
