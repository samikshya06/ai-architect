<!-- source: https://www.geeksforgeeks.org/system-design/design-patterns-architecture/ | provider: jina -->

Title: Software Architectural Patterns in System Design

URL Source:

Published Time: 2023-10-19 04:26:45

Markdown Content:

*   Courses Sale
*   Tutorials
*   Interview Prep

Switch to Dark Mode

Sign In

# Software Architectural Patterns in System Design

Last Updated : 23 Jul, 2025

Design patterns and architectural styles play a crucial role in shaping the structure and behavior of software systems. Let's explore several architectural patterns and styles, each with its characteristics and suitable diagrams.

Table of Content

## ****1. Layered Architecture (N-Tier Architecture)****

Layered Architecture (N-Tier Architecture) is a software design pattern that structures an application into multiple distinct layers, each responsible for specific tasks or concerns. This approach helps in separating different aspects of the application into modular, manageable, and reusable components. Each layer interacts with the one directly above or below it, but layers typically don’t interact with each other directly, promoting a clear separation of concerns.

Layered Architecture

*   ****Presentation Layer (UI)****: This is where the user interacts with the application. It handles user input and displays information.
*   ****Application Layer (Business Logic)****: This layer contains the core logic of the application, such as processing user requests and managing business rules.
*   ****Data Access Layer (Database) :****Data is retrieved and stored. It interacts with the database, ensuring separation of data concerns.

> ****For Example:****In an online e-commerce application, the ****presentation layer**** handles user interaction, the ****business logic layer**** processes orders and payments, and the ****data access layer**** manages database interactions. This separation allows for clear responsibilities and easier maintenance.

This separation of concerns makes the system modular and easier to maintain.

## ****2. Microservices Architecture****

Microservices Architecture is a software architectural style where an application is structured as a collection of small, independent services, each focused on a specific business function. These services are loosely coupled, meaning they can be developed, deployed, and scaled independently of one another.

> ****For Example:**** A e-commerce store platform may have separate microservices for account service, inventory service and shipping service. Each service is independently deployable and communicates via APIs, allowing for scalabilityand flexibility.

## ****3. Service-Oriented Architecture (SOA)****

Service-Oriented Architecture (SOA) is an architectural pattern where an application is designed as a collection of loosely coupled, reusable services that communicate with each other over a network. Each service in an SOA performs a specific business function or operation and can be accessed by other services or external clients. Benefits of SOA include:

*   Services are modular and can be developed, updated, and maintained independently.
*   You can combine existing services to create new applications, improving time-to-market and reducing development costs.
*   SOA allows systems using different technologies or platforms to work together through standardized communication.

Service-Oriented Architecture (SOA)

> ****For Example:****A healthcare system uses SOA where services like ****patient management****, ****appointment scheduling****, and ****billing**** are designed as independent services. Each service communicates over a network to provide a unified experience, often using SOAP or RESTful web services.

## ****4. Event-Driven Architecture (EDA)****

Event-Driven Architecture (EDA) is a software design pattern where the flow of a system is driven by events. An event represents a change in state or an occurrence that other parts of the system can react to. In this architecture, components communicate by producing and consuming events, allowing them to act independently and asynchronously. This makes the system more flexible, scalable, and responsive to real-time actions.

> ****For Example:****In a stock trading application, when a user places an order, an ****event**** is generated. This event triggers other processes such as price updates, notifications, and transaction validation, allowing the system to respond asynchronously to user actions.

## ****5. Hexagonal Architecture (Ports and Adapters)****

The Ports and Adapters pattern, another name for Hexagonal Architecture, is a software design methodology that seeks to provide loosely linked application components. Isolating an application's essential logic (the "inside") from external systems or interfaces (the "outside") is the idea. This is accomplished by drawing distinct lines between the main logic of the application and the external systems—like databases, web services, user interfaces, or messaging systems—that communicate with it.

> ****For Example:****A payment gateway uses hexagonal architecture where the ****core logic**** (payment processing) remains independent of external systems like databases, UI, or third-party services. These external systems interact with the core through specific ****adapters**** (e.g., REST API, database access).

## ****6. Component-Based Architecture****

According to the architectural design pattern known as "component-based architecture," an application is constructed by putting together separate, reusable parts that each contain a particular purpose. Every component is an independent unit that carries out a specific function and provides an open interface for communication. Because these parts can be created, tested, and implemented separately, the system becomes more adaptable, scalable, and modular.

> ****For Example:****In a web application, reusable ****components**** like a ****login form****, ****navigation bar****, and ****product listing**** are created independently and combined to form the UI. Each component encapsulates specific functionality and can be reused across different pages.

## ****7. Blackboard Architecture****

A software design pattern known as "Blackboard Architecture" involves several components (sometimes referred to as "knowledge sources") working together to solve a complex problem by progressively improving a shared data structure termed the "blackboard." While several components (or modules) contribute their knowledge and procedures to alter or improve the answer, the blackboard serves as a common repository for interim findings.

Blackboard Architecture

> ****For Example****: In a medical diagnosis system, different modules (e.g., ****symptom analysis****, ****patient history****, and ****lab tests****) analyze data and contribute to a shared ****blackboard****. As new information is added, other components update the shared knowledge to refine the diagnosis.

## ****8. CQRS (Command Query Responsibility Segregation)****

Common Querry Responsibility Segregation, or CQRS, is a design pattern that divides the task of managing commands and inquiries among several components. Separating the methods for reading and publishing data is the primary goal of the CQRS architectural pattern. It separates the read and update operations on a datastore into two separate models: Queries and Commands, respectively.

> ****For Example:****In an online shopping app, ****Commands**** like placing an order are processed and stored in one model (write model), while ****Queries**** to fetch order details or product listings are handled by a separate read model, optimizing performance and scalability.

## ****9. Serverless architecture****

Developers can create and execute apps using serverless architecture, a cloud computing design style, without having to worry about maintaining the supporting infrastructure. According to this paradigm, server administration, scaling, and resource allocation are automatically handled by cloud providers (like AWS, Azure, or Google Cloud). Developers just concentrate on writing code and designing functions that are run in response to events, rather than deploying and maintaining servers.

> ****For Example:****In a serverless web application, user profile data is processed by cloud functions. Instead of managing servers, developers write functions that execute in response to events (e.g., an HTTP request) and scale automatically with demand, reducing overhead and cost.

## 10. Circuit Breaker Pattern

The Circuit Breaker Pattern is a software design pattern that helps prevent cascading failures in distributed systems. It monitors the health of a service, and if failures reach a certain threshold, it "trips" the circuit, stopping requests to the failing service. This prevents the system from overloading the service with more requests, giving it time to recover.

> ****For Example:****
>
>
> *   Just like an electrical circuit breaker, when an issue is detected, the circuit "opens" to stop the flow of requests.
> *   After a set time, the circuit "closes" again if the service is healthy. This allows the system to fail gracefully and recover without spreading the failure to other parts of the system.

## 11. Model-view-controller pattern

The Model-View-Controller (MVC) pattern is a software architectural design that separates an application into three interconnected components: Model, View, and Controller. This separation helps organize code by decoupling the business logic, user interface, and user input handling, which promotes modularity, maintainability, and scalability.

*   ****Model****: Represents the core data and business logic of the application. It is responsible for retrieving, storing, and processing data, and updating the ****View**** when data changes.
*   ****View****: The user interface (UI) component that displays the data to the user and responds to user interactions. The ****View**** is updated whenever the ****Model**** changes.
*   ****Controller****: Acts as an intermediary between the ****Model**** and the ****View****. It handles user input, updates the ****Model**** accordingly, and updates the ****View**** to reflect any changes in the ****Model****.

## Conclusion

These architectural patterns and styles provide a framework for designing and building robust, scalable, and maintainable software systems. The choice of architecture depends on the specific requirements and constraints of your project. Each of these patterns comes with its own advantages and trade-offs, so it's important to select the one that best fits your needs.

